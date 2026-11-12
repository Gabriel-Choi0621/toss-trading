"""
리스크 관리자
주문 전 리스크 체크를 담당합니다.
"""
import asyncio
import logging
from datetime import datetime, timedelta

from config_loader import get

logger = logging.getLogger(__name__)


class RiskManager:
    """
    주문 전 리스크 체크.
    - 단일 종목 최대 비중
    - 일일 최대 손실 한도
    - 연속 손절 횟수 한도 + 쿨다운
    """

    def __init__(self):
        cfg = get("trading.risk", {})
        self.max_position_ratio: float = cfg.get("max_position_ratio", 0.20)
        self.max_total_position_ratio: float = cfg.get("max_total_position_ratio", 0.40)
        self.daily_loss_limit: float = cfg.get("daily_loss_limit", 0.03)
        self.consecutive_stop_limit: int = cfg.get("consecutive_stop_limit", 3)
        self.cooldown_minutes: int = cfg.get("cooldown_minutes", 60)

        self._daily_loss: float = 0.0
        self._daily_loss_date: datetime = datetime.now().date()
        self._consecutive_stops: int = 0
        self._cooldown_until: datetime | None = None

    def _reset_daily_if_needed(self) -> None:
        today = datetime.now().date()
        if today != self._daily_loss_date:
            self._daily_loss = 0.0
            self._daily_loss_date = today
            self._consecutive_stops = 0
            logger.info("새 거래일 — 일일 손실/연속손절 초기화")

    def check(self, ticker: str, order_value: int, total_asset: int,
              current_holding_value: int = 0) -> tuple[bool, str]:
        """
        매수 주문 가능 여부를 확인합니다.
        current_holding_value: 해당 종목 기존 보유 평가금액

        Returns:
            (ok: bool, reason: str)
        """
        self._reset_daily_if_needed()

        # 쿨다운 중인지 확인
        if self._cooldown_until and datetime.now() < self._cooldown_until:
            remaining = int((self._cooldown_until - datetime.now()).total_seconds()) // 60
            return False, f"연속 손절 후 쿨다운 중 (잔여 {remaining}분)"

        # 일일 최대 손실 초과
        if total_asset > 0:
            loss_ratio = self._daily_loss / total_asset
            if loss_ratio >= self.daily_loss_limit:
                return False, f"일일 손실 한도 초과 ({loss_ratio:.1%} >= {self.daily_loss_limit:.1%})"

        if total_asset > 0 and order_value > 0:
            # 1회 주문 비중 초과 (단일 주문 크기 제한)
            order_ratio = order_value / total_asset
            if order_ratio > self.max_position_ratio:
                return False, (
                    f"단일 주문 비중 초과 "
                    f"({order_ratio:.1%} > {self.max_position_ratio:.1%})"
                )

            # 총 보유 비중 초과 (기존 + 신규)
            total_ratio = (current_holding_value + order_value) / total_asset
            if total_ratio > self.max_total_position_ratio:
                return False, (
                    f"종목 총 보유 한도 초과 "
                    f"({total_ratio:.1%} > {self.max_total_position_ratio:.1%})"
                )

        return True, "OK"

    def check_sell(self, total_asset: int, force: bool = False) -> tuple[bool, str]:
        """
        매도 주문 가능 여부 확인 (쿨다운·일일 손실만 체크).

        Args:
            total_asset: 총 자산
            force: True이면 쿨다운·일일 손실 한도 무시 (손절가 강제 매도용)
        """
        self._reset_daily_if_needed()

        if force:
            # 손절가 도달 시 강제 매도 — 쿨다운·일일 손실 한도 무관
            logger.info("force=True — 쿨다운/일일 손실 한도 무시하고 매도 진행")
            return True, "OK (forced)"

        if self._cooldown_until and datetime.now() < self._cooldown_until:
            remaining = int((self._cooldown_until - datetime.now()).total_seconds()) // 60
            return False, f"연속 손절 후 쿨다운 중 (잔여 {remaining}분)"

        if total_asset > 0:
            loss_ratio = self._daily_loss / total_asset
            if loss_ratio >= self.daily_loss_limit:
                return False, f"일일 손실 한도 초과 ({loss_ratio:.1%} >= {self.daily_loss_limit:.1%})"

        return True, "OK"

    def record_loss(self, loss_amount: float) -> None:
        """손실을 기록하고 연속 손절 카운트를 업데이트합니다."""
        self._daily_loss += loss_amount
        self._consecutive_stops += 1
        logger.info(
            f"손실 기록: {loss_amount:,.0f}원 | "
            f"일일 누계: {self._daily_loss:,.0f}원 | "
            f"연속 손절: {self._consecutive_stops}회"
        )

        if self._consecutive_stops >= self.consecutive_stop_limit:
            self._cooldown_until = datetime.now() + timedelta(minutes=self.cooldown_minutes)
            logger.warning(
                f"연속 손절 {self._consecutive_stops}회 — "
                f"{self.cooldown_minutes}분 매매 중단 시작"
            )

    def record_profit(self) -> None:
        """수익 발생 시 연속 손절 카운트를 초기화합니다."""
        self._consecutive_stops = 0

    @property
    def status(self) -> dict:
        return {
            "daily_loss": self._daily_loss,
            "consecutive_stops": self._consecutive_stops,
            "cooldown_until": self._cooldown_until.isoformat() if self._cooldown_until else None,
        }
