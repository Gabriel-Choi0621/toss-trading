"""
AI Judge 엔진
Gemini 3.1 Flash-Lite API를 호출해 매수/매도/홀딩 신호를 생성합니다.

실측: 68회+ 연속 성공, RPD 한도 미확인 (RPM에 먼저 걸림)
→ 3종목 × 5분 간격 = 하루 약 234회 커버 가능
동시 호출 방지: asyncio.Semaphore(1)
"""
import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, date
from pathlib import Path
from typing import Literal

from ai.prompt_templates import SYSTEM_PROMPT_KR, SYSTEM_PROMPT_US, build_user_prompt
from config_loader import get

logger = logging.getLogger(__name__)

try:
    from google import genai
    from google.genai import types
    _GENAI_AVAILABLE = True
except ImportError:
    _GENAI_AVAILABLE = False
    logger.warning("google-genai 미설치. pip install google-genai")


@dataclass
class JudgmentResult:
    ticker: str
    signal: Literal["BUY", "SELL", "HOLD"]
    confidence: int
    target_price: float   # KR: 정수값 float, US: 소수점 2자리
    stop_loss: float      # KR: 정수값 float, US: 소수점 2자리
    target_return_pct: float
    stop_loss_pct: float
    reason: str
    risk_level: Literal["low", "medium", "high"]
    input_tokens: int = 0
    output_tokens: int = 0
    manual_qty: int = 0  # 수동 주문 시 UI에서 지정한 수량 (0이면 자동 계산)

    def __str__(self) -> str:
        return (
            f"[{self.ticker}] {self.signal} "
            f"신뢰도={self.confidence}% "
            f"목표={self.target_price:,} "
            f"손절={self.stop_loss:,} "
            f"리스크={self.risk_level}"
        )


def _safe_hold(ticker: str, reason: str) -> JudgmentResult:
    return JudgmentResult(
        ticker=ticker,
        signal="HOLD",
        confidence=0,
        target_price=0.0,
        stop_loss=0.0,
        target_return_pct=0.0,
        stop_loss_pct=0.0,
        reason=reason,
        risk_level="high",
    )


_RATE_LIMIT_FILE = Path("data/gemini_usage.json")


class RateLimiter:
    """
    Gemini 무료 티어 한도를 초과하지 않도록 관리합니다.
    - RPD(하루 요청 수): 재시작해도 오늘 누적 사용량 유지
    - RPM(분당 요청 수): 메모리에만 유지 (분 단위라 재시작 시 초기화 무방)
    """

    def __init__(self, rpd: int = 1000, rpm: int = 15):
        self.rpd = rpd
        self.rpm = rpm
        self._daily_date: date = datetime.now().date()
        self._minute_timestamps: list[float] = []
        # 오늘 사용량을 파일에서 복원
        self._daily_count: int = self._load()

    def _load(self) -> int:
        """오늘 날짜의 사용량을 파일에서 복원합니다."""
        try:
            if _RATE_LIMIT_FILE.exists():
                data = json.loads(_RATE_LIMIT_FILE.read_text(encoding="utf-8"))
                if data.get("date") == str(self._daily_date):
                    count = int(data.get("count", 0))
                    logger.info(f"Gemini 일일 사용량 복원: {count}/{self.rpd}회")
                    return count
        except Exception as e:
            logger.warning(f"Gemini 사용량 복원 실패: {e}")
        return 0

    def _save(self) -> None:
        """현재 사용량을 파일에 저장합니다."""
        try:
            _RATE_LIMIT_FILE.parent.mkdir(parents=True, exist_ok=True)
            _RATE_LIMIT_FILE.write_text(
                json.dumps({"date": str(self._daily_date), "count": self._daily_count}),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning(f"Gemini 사용량 저장 실패: {e}")

    def can_call(self) -> tuple[bool, str]:
        """호출 가능 여부를 반환합니다."""
        today = datetime.now().date()
        if today != self._daily_date:
            self._daily_count = 0
            self._daily_date = today
            self._save()
            logger.info("새 거래일 — Gemini 일일 호출 카운트 초기화")

        if self._daily_count >= self.rpd:
            return False, f"일일 한도 도달 ({self._daily_count}/{self.rpd}회) — 내일 초기화"

        now = time.time()
        self._minute_timestamps = [t for t in self._minute_timestamps if now - t < 60]
        if len(self._minute_timestamps) >= self.rpm:
            wait = 60 - (now - self._minute_timestamps[0])
            return False, f"분당 한도 도달 — {wait:.0f}초 후 재시도"

        return True, "OK"

    def seconds_until_available(self) -> float:
        """다음 호출까지 대기해야 할 초(0이면 즉시 가능)."""
        now = time.time()
        self._minute_timestamps = [t for t in self._minute_timestamps if now - t < 60]
        if len(self._minute_timestamps) < self.rpm:
            return 0.0
        return max(0.0, 60.0 - (now - self._minute_timestamps[0]))

    def record_call(self) -> None:
        self._daily_count += 1
        self._minute_timestamps.append(time.time())
        self._save()  # 호출할 때마다 파일에 저장

    @property
    def status(self) -> dict:
        today = datetime.now().date()
        if today != self._daily_date:
            return {"daily": 0, "daily_limit": self.rpd, "remaining": self.rpd}
        return {
            "daily": self._daily_count,
            "daily_limit": self.rpd,
            "remaining": self.rpd - self._daily_count,
        }


# 전역 Semaphore — 동시에 1건만 Gemini 호출 (모든 JudgmentEngine 인스턴스 공유)
_GEMINI_SEMAPHORE = asyncio.Semaphore(1)
# 종목 간 최소 간격 (초): 60 / 5 RPM = 12초, 여유분 추가
_MIN_CALL_INTERVAL = 13.0
_last_call_time: float = 0.0


class JudgmentEngine:
    """Gemini 3.1 Flash-Lite 기반 매매 판단 엔진 (무료 티어).

    실측 68회+ 연속 성공, RPM 한도(15)에 먼저 걸림 → RPD 여유 충분
    """

    def __init__(self):
        api_key = get("gemini.api_key", "")
        self.model_name = get("gemini.model", "gemini-3.1-flash-lite")
        self._model = None
        self._client = None  # 반드시 먼저 None으로 초기화 (early return 대비)
        self.market: str = "KR"
        self.rate_limiter = RateLimiter(
            rpd=get("gemini.daily_limit", 500),
            rpm=get("gemini.rpm_limit", 15),
        )
        # 종목별 마지막 판단 시각 (RPD 절약용 쿨다운)
        self._last_judged_at: dict[str, float] = {}
        self._min_interval: float = get("gemini.min_interval_minutes", 5) * 60
        # 현재 활성 종목 수 — main.py에서 갱신 (adaptive interval 계산용)
        self.n_tickers: int = 1
        self._total_calls: int = 0

        if not _GENAI_AVAILABLE:
            logger.error("google-genai 패키지 없음 — pip install google-genai")
            return

        if not api_key or api_key == "YOUR_GEMINI_API_KEY":
            logger.warning("Gemini API 키 미설정 — HOLD만 반환됩니다")
            return

        self._client = genai.Client(api_key=api_key)
        logger.info(f"Gemini 엔진 초기화 완료 (모델: {self.model_name})")

    def set_market(self, market: str) -> None:
        """시장 모드를 변경합니다 ('KR' | 'US')."""
        self.market = market
        logger.info(f"JudgmentEngine 시장 변경 → {market}")

    def _get_effective_interval(self) -> float:
        """남은 일일 예산을 자정까지 균등 배분한 종목당 최소 판단 간격(초)을 반환.

        예) 잔여 6회 / 3종목 = 2사이클, 자정까지 4시간 = 7200초
            → 사이클당 간격 = 7200 / 2 = 3600초 (60분)
        항상 config 하한(min_interval)과의 max 값을 사용.
        """
        status = self.rate_limiter.status
        remaining = max(0, status["remaining"])

        if remaining < self.n_tickers:
            # 이번 사이클 완성도 불가능 — 오늘 예산 소진
            return float("inf")

        now = datetime.now()
        secs_to_midnight = float(
            (24 * 3600) - (now.hour * 3600 + now.minute * 60 + now.second)
        )

        # 남은 예산으로 만들 수 있는 완전 사이클 수
        full_cycles = remaining / max(self.n_tickers, 1)
        if full_cycles <= 0:
            return float("inf")

        adaptive = secs_to_midnight / full_cycles
        effective = max(self._min_interval, adaptive)

        logger.debug(
            f"adaptive interval: 잔여 {remaining}회 / {self.n_tickers}종목 "
            f"= {full_cycles:.1f}사이클, 자정까지 {secs_to_midnight/3600:.1f}h "
            f"→ {effective/60:.0f}분 간격"
        )
        return effective

    def set_model(self, model_name: str) -> None:
        """Gemini 모델을 런타임에 변경합니다."""
        self.model_name = model_name
        logger.info(f"Gemini 모델 변경 → {model_name}")

    async def judge(
        self,
        ticker: str,
        indicators: dict,
        position: dict = None,
        prev_signal: str = None,
        prev_confidence: int = None,
        daily_indicators: dict = None,
    ) -> JudgmentResult:
        """
        지표를 분석하여 매매 신호를 반환합니다.

        동시 호출 방지를 위해 전역 Semaphore를 사용합니다.
        429 응답 시 retryDelay만큼 대기 후 재시도합니다.

        Args:
            ticker: 종목 코드
            indicators: technical.analyze() 반환값
            position: 현재 보유 포지션 dict (qty, avg_price, current_price)
            prev_signal: 직전 AI 판단 신호
            prev_confidence: 직전 AI 판단 신뢰도
            daily_indicators: 일봉 지표 (멀티 타임프레임)

        Returns:
            JudgmentResult (한도 초과 또는 오류 시 HOLD 반환)
        """
        if not self._client:
            return _safe_hold(ticker, "Gemini API 키 미설정")

        # 종목별 최소 재판단 간격 체크 (adaptive: 남은 예산 균등 배분)
        effective_interval = self._get_effective_interval()
        if effective_interval == float("inf"):
            logger.warning(f"[{ticker}] 오늘 Gemini 예산 소진 → HOLD")
            return _safe_hold(ticker, "오늘 AI 판단 예산 소진 — 내일 초기화")

        now_ts = time.time()
        last_ts = self._last_judged_at.get(ticker, 0.0)
        elapsed = now_ts - last_ts
        if elapsed < effective_interval:
            remaining_min = (effective_interval - elapsed) / 60
            logger.debug(
                f"[{ticker}] 재판단 쿨다운 중 ({remaining_min:.0f}분 남음) → 직전 신호 유지"
            )
            return _safe_hold(ticker, f"쿨다운 중 ({remaining_min:.0f}분 후 재판단)")

        # 일일 한도 사전 체크
        ok, reason = self.rate_limiter.can_call()
        if not ok and "일일" in reason:
            logger.warning(f"[{ticker}] Gemini 한도: {reason} → HOLD 반환")
            return _safe_hold(ticker, reason)

        prompt = build_user_prompt(
            ticker, indicators,
            market=self.market,
            position=position,
            prev_signal=prev_signal,
            prev_confidence=prev_confidence,
            daily_indicators=daily_indicators,
        )
        system_prompt = SYSTEM_PROMPT_US if self.market == "US" else SYSTEM_PROMPT_KR

        # ── 전역 Semaphore로 직렬화 ────────────────────────────────────────────
        async with _GEMINI_SEMAPHORE:
            return await self._call_with_retry(ticker, prompt, system_prompt, indicators)

    async def _call_with_retry(
        self,
        ticker: str,
        prompt: str,
        system_prompt: str,
        indicators: dict,
        max_attempts: int = 3,
    ) -> JudgmentResult:
        """Semaphore 내부에서 실행되는 실제 API 호출 + 재시도 로직."""
        global _last_call_time

        last_error = None

        for attempt in range(1, max_attempts + 1):
            # ── 최소 호출 간격 보장 ────────────────────────────────────────────
            elapsed = time.time() - _last_call_time
            if elapsed < _MIN_CALL_INTERVAL:
                wait_for = _MIN_CALL_INTERVAL - elapsed
                logger.debug(f"[{ticker}] 최소 간격 대기 {wait_for:.1f}초")
                await asyncio.sleep(wait_for)

            # ── 분당 한도 대기 ─────────────────────────────────────────────────
            wait_rpm = self.rate_limiter.seconds_until_available()
            if wait_rpm > 0:
                logger.info(f"[{ticker}] RPM 한도 — {wait_rpm:.0f}초 대기 (시도 {attempt})")
                await asyncio.sleep(wait_rpm + 1)

            try:
                _last_call_time = time.time()
                response = self._client.models.generate_content(
                    model=self.model_name,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        system_instruction=system_prompt,
                        temperature=0.1,
                        max_output_tokens=512,
                        response_mime_type="application/json",  # JSON 강제 포맷
                    ),
                )
                self.rate_limiter.record_call()
                self._total_calls += 1
                self._last_judged_at[ticker] = time.time()  # 쿨다운 시작

                raw = response.text.strip()
                data = _parse_json(raw)

                usage = getattr(response, "usage_metadata", None)
                input_tok = getattr(usage, "prompt_token_count", 0) if usage else 0
                output_tok = getattr(usage, "candidates_token_count", 0) if usage else 0

                logger.info(
                    f"[{ticker}] Gemini 판단 완료 | "
                    f"일일 {self.rate_limiter.status['daily']}/{self.rate_limiter.status['daily_limit']}회 사용"
                )

                raw_target = data.get("target_price", indicators.get("current_price", 0))
                raw_stop   = data.get("stop_loss", 0)
                return JudgmentResult(
                    ticker=ticker,
                    signal=data.get("signal", "HOLD"),
                    confidence=int(data.get("confidence", 0)),
                    target_price=round(float(raw_target), 2),
                    stop_loss=round(float(raw_stop), 2),
                    target_return_pct=float(data.get("target_return_pct", 0.0)),
                    stop_loss_pct=float(data.get("stop_loss_pct", 0.0)),
                    reason=data.get("reason", ""),
                    risk_level=data.get("risk_level", "medium"),
                    input_tokens=input_tok,
                    output_tokens=output_tok,
                )

            except (json.JSONDecodeError, KeyError, ValueError) as e:
                last_error = f"JSON 파싱 실패 ({attempt}/{max_attempts}): {e}"
                logger.warning(f"[{ticker}] {last_error}")
                # JSON 오류는 재시도해도 같은 결과일 가능성 — 짧게 대기 후 재시도
                if attempt < max_attempts:
                    await asyncio.sleep(2)

            except Exception as e:
                err_str = str(e)
                last_error = f"API 오류 ({attempt}/{max_attempts}): {e}"
                logger.error(f"[{ticker}] {last_error}")

                if "429" in err_str or "quota" in err_str.lower() or "RESOURCE_EXHAUSTED" in err_str:
                    # retryDelay 파싱 (예: "'retryDelay': '58s'")
                    delay = _parse_retry_delay(err_str)
                    logger.warning(
                        f"[{ticker}] 429 한도 초과 — {delay:.0f}초 대기 후 재시도 ({attempt}/{max_attempts})"
                    )
                    if attempt < max_attempts:
                        await asyncio.sleep(delay)
                    else:
                        break
                elif "503" in err_str or "UNAVAILABLE" in err_str or "high demand" in err_str.lower():
                    # 서버 과부하 — 지수 백오프 (30s, 60s, 120s)
                    delay = 30.0 * (2 ** (attempt - 1))
                    logger.warning(
                        f"[{ticker}] 503 서버 과부하 — {delay:.0f}초 대기 후 재시도 ({attempt}/{max_attempts})"
                    )
                    if attempt < max_attempts:
                        await asyncio.sleep(delay)
                    else:
                        break
                else:
                    # 기타 API 오류는 짧게 대기 후 재시도
                    if attempt < max_attempts:
                        await asyncio.sleep(5)

        logger.error(f"[{ticker}] 판단 실패 → HOLD 반환. 마지막 오류: {last_error}")
        return _safe_hold(ticker, f"판단 실패: {last_error}")

    @property
    def total_calls(self) -> int:
        return self._total_calls

    @property
    def rate_limit_status(self) -> dict:
        return self.rate_limiter.status


# ── 유틸 함수 ────────────────────────────────────────────────────────────────

def _parse_retry_delay(err_str: str, default: float = 62.0) -> float:
    """
    API 오류 문자열에서 retryDelay 값을 파싱합니다.
    예: "'retryDelay': '58s'" → 62.0 (버퍼 4초 추가)
    """
    match = re.search(r"retryDelay['\"]?\s*:\s*['\"]?(\d+)s", err_str)
    if match:
        return float(match.group(1)) + 4  # 4초 여유
    return default


def _parse_json(text: str) -> dict:
    """
    응답 텍스트에서 JSON 객체를 추출합니다.

    처리 순서:
    1. 코드 펜스(```json ... ```) 제거
    2. 직접 json.loads() 시도
    3. 실패 시 regex로 { ... } 블록 추출
    4. 추출된 텍스트에서 trailing comma / JS 주석 제거 후 재시도
    """
    text = text.strip()

    # 1. 코드 펜스 제거
    if text.startswith("```"):
        lines = text.split("\n")
        # 첫 줄(```json 등)과 마지막 줄(```) 제거
        inner = lines[1:]
        if inner and inner[-1].strip() == "```":
            inner = inner[:-1]
        text = "\n".join(inner).strip()

    # 2. 직접 파싱 시도
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 3. regex로 첫 번째 { ... } 블록 추출
    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        candidate = match.group(0)

        # 4a. 추출된 블록 직접 파싱
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass

        # 4b. trailing comma 제거: ,} 또는 ,]
        cleaned = re.sub(r",\s*([}\]])", r"\1", candidate)
        # 4c. 한 줄 주석 제거 (// ...)
        cleaned = re.sub(r"//[^\n]*", "", cleaned)
        # 4d. 블록 주석 제거 (/* ... */)
        cleaned = re.sub(r"/\*[\s\S]*?\*/", "", cleaned)

        try:
            return json.loads(cleaned)
        except json.JSONDecodeError as e:
            raise json.JSONDecodeError(
                f"JSON 정제 후에도 파싱 실패: {e.msg}", e.doc, e.pos
            ) from e

    raise json.JSONDecodeError("JSON 객체를 찾을 수 없음", text, 0)
