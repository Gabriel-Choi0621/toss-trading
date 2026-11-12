"""
기술적 지표 분석기
pandas-ta를 사용합니다 (TA-Lib 없이 설치 가능).
"""
import logging
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

try:
    import pandas_ta as ta
    _TA_AVAILABLE = True
except ImportError:
    logger.warning("pandas-ta 미설치. pip install pandas-ta 실행 후 사용하세요.")
    _TA_AVAILABLE = False


def analyze(df: pd.DataFrame) -> Optional[dict]:
    """
    OHLCV DataFrame으로 기술적 지표를 계산합니다.

    Args:
        df: columns [open, high, low, close, volume], 최소 60행 권장

    Returns:
        지표 딕셔너리 또는 None (데이터 부족 시)
    """
    if df is None or len(df) < 30:
        logger.warning(f"데이터 부족 ({len(df) if df is not None else 0}행) — 지표 계산 생략")
        return None

    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    volume = df["volume"].astype(float)

    result: dict = {}

    # ── RSI ──────────────────────────────────────────────────────────────────
    try:
        if _TA_AVAILABLE:
            rsi_series = ta.rsi(close, length=14)
            result["rsi"] = _last(rsi_series)
        else:
            result["rsi"] = _calc_rsi(close, 14)
    except Exception as e:
        logger.warning(f"RSI 계산 오류: {e}")
        result["rsi"] = None

    # ── MACD ─────────────────────────────────────────────────────────────────
    try:
        if _TA_AVAILABLE:
            macd_df = ta.macd(close, fast=12, slow=26, signal=9)
            if macd_df is not None and not macd_df.empty:
                macd_val = _last(macd_df.iloc[:, 0])
                signal_val = _last(macd_df.iloc[:, 2])
                result["macd"] = macd_val
                result["macd_signal"] = signal_val
                result["macd_histogram"] = _last(macd_df.iloc[:, 1])
                # 크로스 방향: 1=골든, -1=데드, 0=없음
                result["macd_cross"] = _macd_cross(macd_df)
            else:
                result.update({"macd": None, "macd_signal": None, "macd_histogram": None, "macd_cross": 0})
        else:
            result.update({"macd": None, "macd_signal": None, "macd_histogram": None, "macd_cross": 0})
    except Exception as e:
        logger.warning(f"MACD 계산 오류: {e}")
        result.update({"macd": None, "macd_signal": None, "macd_histogram": None, "macd_cross": 0})

    # ── 볼린저밴드 ────────────────────────────────────────────────────────────
    try:
        if _TA_AVAILABLE:
            bb = ta.bbands(close, length=20, std=2)
            if bb is not None and not bb.empty:
                bb_upper = _last(bb.iloc[:, 0])
                bb_mid = _last(bb.iloc[:, 1])
                bb_lower = _last(bb.iloc[:, 2])
                cur_price = close.iloc[-1]
                band_width = bb_upper - bb_lower
                bb_position = (cur_price - bb_lower) / band_width if band_width > 0 else 0.5
                result.update({
                    "bb_upper": bb_upper,
                    "bb_mid": bb_mid,
                    "bb_lower": bb_lower,
                    "bb_position": round(bb_position, 3),  # 0~1 (0=하단, 1=상단)
                })
            else:
                result.update({"bb_upper": None, "bb_mid": None, "bb_lower": None, "bb_position": None})
        else:
            result.update({"bb_upper": None, "bb_mid": None, "bb_lower": None, "bb_position": None})
    except Exception as e:
        logger.warning(f"볼린저밴드 계산 오류: {e}")
        result.update({"bb_upper": None, "bb_mid": None, "bb_lower": None, "bb_position": None})

    # ── 이동평균 ──────────────────────────────────────────────────────────────
    try:
        ma5 = close.rolling(5).mean().iloc[-1]
        ma20 = close.rolling(20).mean().iloc[-1]
        ma60 = close.rolling(min(60, len(close))).mean().iloc[-1]
        cur = close.iloc[-1]

        # 정배열: 5 > 20 > 60 / 역배열: 5 < 20 < 60
        if ma5 > ma20 > ma60:
            ma_arrangement = "bullish"
        elif ma5 < ma20 < ma60:
            ma_arrangement = "bearish"
        else:
            ma_arrangement = "neutral"

        result.update({
            "ma5": round(float(ma5), 1),
            "ma20": round(float(ma20), 1),
            "ma60": round(float(ma60), 1),
            "ma_arrangement": ma_arrangement,
            "price_vs_ma20": round((cur - ma20) / ma20 * 100, 2),  # 현재가가 MA20 대비 몇 %
        })
    except Exception as e:
        logger.warning(f"이동평균 계산 오류: {e}")
        result.update({"ma5": None, "ma20": None, "ma60": None, "ma_arrangement": None, "price_vs_ma20": None})

    # ── 거래량 비율 ───────────────────────────────────────────────────────────
    try:
        vol_avg = volume.rolling(20).mean().iloc[-1]
        vol_ratio = volume.iloc[-1] / vol_avg if vol_avg > 0 else 1.0
        result["volume_ratio"] = round(float(vol_ratio), 2)
    except Exception as e:
        logger.warning(f"거래량 비율 계산 오류: {e}")
        result["volume_ratio"] = None

    # ── 지지/저항 (볼린저밴드 기준) ──────────────────────────────────────────
    result["support"] = result.get("bb_lower")
    result["resistance"] = result.get("bb_upper")

    # ── 현재가 추가 ───────────────────────────────────────────────────────────
    # KR은 원화 정수지만, US 주식 소수점 손실 방지를 위해 float 유지
    result["current_price"] = round(float(close.iloc[-1]), 2)

    logger.debug(f"지표 계산 완료: {list(result.keys())}")
    return result


def analyze_daily(df: pd.DataFrame) -> Optional[dict]:
    """
    일봉 OHLCV DataFrame으로 중장기 지표를 계산합니다.

    Args:
        df: 일봉 OHLCV DataFrame (최소 60행 권장, 120행 이상 이상적)

    Returns:
        {
            "daily_ma20", "daily_ma60", "daily_ma120",
            "daily_rsi", "daily_ma_arrangement",
            "week52_high", "week52_low", "week52_position",
            "price_vs_daily_ma60"
        }
    """
    if df is None or len(df) < 20:
        logger.warning(f"일봉 데이터 부족 ({len(df) if df is not None else 0}행) — 일봉 지표 생략")
        return None

    close = df["close"].astype(float)
    high  = df["high"].astype(float)
    low   = df["low"].astype(float)
    cur   = close.iloc[-1]

    result: dict = {}

    # ── 이동평균 ──────────────────────────────────────────────────────────────
    try:
        ma20  = close.rolling(min(20,  len(close))).mean().iloc[-1]
        ma60  = close.rolling(min(60,  len(close))).mean().iloc[-1]
        ma120 = close.rolling(min(120, len(close))).mean().iloc[-1]

        if ma20 > ma60 > ma120:
            daily_trend = "bullish"
        elif ma20 < ma60 < ma120:
            daily_trend = "bearish"
        else:
            daily_trend = "neutral"

        result.update({
            "daily_ma20": round(float(ma20), 2),
            "daily_ma60": round(float(ma60), 2),
            "daily_ma120": round(float(ma120), 2),
            "daily_ma_arrangement": daily_trend,
            "price_vs_daily_ma60": round((cur - ma60) / ma60 * 100, 2) if ma60 > 0 else None,
        })
    except Exception as e:
        logger.warning(f"일봉 이동평균 계산 오류: {e}")
        result.update({
            "daily_ma20": None, "daily_ma60": None, "daily_ma120": None,
            "daily_ma_arrangement": None, "price_vs_daily_ma60": None,
        })

    # ── 일봉 RSI ──────────────────────────────────────────────────────────────
    try:
        if _TA_AVAILABLE:
            rsi_s = ta.rsi(close, length=14)
            result["daily_rsi"] = _last(rsi_s)
        else:
            result["daily_rsi"] = _calc_rsi(close, 14)
    except Exception as e:
        logger.warning(f"일봉 RSI 오류: {e}")
        result["daily_rsi"] = None

    # ── 52주 고저 ─────────────────────────────────────────────────────────────
    try:
        window = min(252, len(df))  # 약 252 거래일 = 1년
        w52_high = float(high.rolling(window).max().iloc[-1])
        w52_low  = float(low.rolling(window).min().iloc[-1])
        rng = w52_high - w52_low
        w52_pos  = round((cur - w52_low) / rng * 100, 1) if rng > 0 else 50.0

        result.update({
            "week52_high": round(w52_high, 2),
            "week52_low":  round(w52_low,  2),
            "week52_position": w52_pos,  # 0%=52주 저점, 100%=52주 고점
        })
    except Exception as e:
        logger.warning(f"52주 고저 계산 오류: {e}")
        result.update({"week52_high": None, "week52_low": None, "week52_position": None})

    logger.debug(f"일봉 지표 계산 완료: {list(result.keys())}")
    return result


# ── 내부 헬퍼 ─────────────────────────────────────────────────────────────────

def _last(series: pd.Series) -> Optional[float]:
    """시리즈의 마지막 유효값을 반환합니다."""
    if series is None or series.empty:
        return None
    val = series.dropna()
    return float(val.iloc[-1]) if not val.empty else None


def _macd_cross(macd_df: pd.DataFrame) -> int:
    """
    최근 MACD 크로스 방향을 반환합니다.
    1 = 골든크로스 (MACD가 Signal 상향 돌파)
    -1 = 데드크로스 (MACD가 Signal 하향 돌파)
    0 = 크로스 없음
    """
    try:
        macd = macd_df.iloc[:, 0].dropna()
        signal = macd_df.iloc[:, 2].dropna()
        if len(macd) < 2 or len(signal) < 2:
            return 0
        prev_diff = macd.iloc[-2] - signal.iloc[-2]
        curr_diff = macd.iloc[-1] - signal.iloc[-1]
        if prev_diff < 0 and curr_diff >= 0:
            return 1
        if prev_diff > 0 and curr_diff <= 0:
            return -1
        return 0
    except Exception:
        return 0


def _calc_rsi(close: pd.Series, period: int = 14) -> Optional[float]:
    """pandas만 사용한 RSI 계산 (pandas-ta 없을 때 폴백)."""
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = 100 - 100 / (1 + rs)
    return _last(rsi)
