"""
Mock 데이터 생성기
토스증권 API가 공개되기 전까지 실제 시세 데이터를 대체합니다.
야후 파이낸스 또는 랜덤 생성 방식을 지원합니다.
"""
import asyncio
import logging
import random
from datetime import datetime, timedelta
from typing import Callable, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def generate_ohlcv(
    ticker: str,
    count: int = 100,
    base_price: Optional[float] = None,
    volatility: float = 0.015,
) -> pd.DataFrame:
    """
    랜덤 OHLCV 데이터를 생성합니다.

    Args:
        ticker: 종목 코드 (예: "005930")
        count: 캔들 수
        base_price: 기준 가격 (None이면 종목별 기본값 사용)
        volatility: 일일 변동성

    Returns:
        columns: [open, high, low, close, volume]
    """
    # 종목별 기본 가격 (실제 수준에 맞게 설정)
    default_prices = {
        "005930": 75000,   # 삼성전자
        "000660": 180000,  # SK하이닉스
        "035420": 220000,  # NAVER
        "AAPL": 191.0,    # Apple
        "TSLA": 248.0,    # Tesla
        "NVDA": 875.0,    # NVIDIA
    }
    price = base_price or default_prices.get(ticker, 50000)
    is_us = ticker.isalpha() and ticker.isupper() and not ticker.isdigit()

    now = datetime.now()
    timestamps = [now - timedelta(minutes=count - i) for i in range(count)]

    closes = [price]
    for _ in range(count - 1):
        change = random.gauss(0, volatility)
        closes.append(max(closes[-1] * (1 + change), 1000))

    def _round(v: float) -> float:
        return round(v, 2) if is_us else round(v, -1)

    data = []
    for i, close in enumerate(closes):
        open_ = closes[i - 1] if i > 0 else close * (1 + random.gauss(0, 0.003))
        high = max(open_, close) * (1 + abs(random.gauss(0, 0.005)))
        low = min(open_, close) * (1 - abs(random.gauss(0, 0.005)))
        volume = int(random.gauss(500_000, 150_000))
        data.append({
            "open": _round(open_),
            "high": _round(high),
            "low": _round(low),
            "close": _round(close),
            "volume": max(volume, 10_000),
        })

    df = pd.DataFrame(data, index=pd.DatetimeIndex(timestamps))
    df.index.name = "datetime"
    return df


# ─────────────────────────────────────────────────────────────────────────────
# 토스증권 API 인터페이스 (공개 후 이 클래스를 실제 API로 교체)
# ─────────────────────────────────────────────────────────────────────────────

class TossAPIInterface:
    """
    토스증권 API 추상 인터페이스.
    현재는 Mock으로 동작하며, 실제 연동 시 이 부분을 구현합니다.

    TODO: 실제 연동 시 구현 필요 항목
      - REST: 현재가 조회, 잔고 조회, 주문 실행, 주문 취소
      - WebSocket: 실시간 시세 구독
      - 인증: OAuth2 또는 API Key 방식
    """

    def __init__(self, app_key: str = "", app_secret: str = "", mock: bool = True):
        self.app_key = app_key
        self.app_secret = app_secret
        self.mock = mock
        logger.info(f"TossAPI 초기화 (mock={mock})")

    # ── 시세 조회 ─────────────────────────────────────────────────────────────

    async def get_current_price(self, ticker: str) -> dict:
        """현재가 조회."""
        if self.mock:
            df = generate_ohlcv(ticker, count=1)
            row = df.iloc[-1]
            is_us = ticker.isalpha() and ticker.isupper() and not ticker.isdigit()
            price = round(float(row["close"]), 2) if is_us else int(row["close"])
            return {
                "ticker": ticker,
                "price": price,
                "volume": int(row["volume"]),
                "timestamp": datetime.now().isoformat(),
            }
        # TODO: 실제 REST 구현
        raise NotImplementedError("실제 API 연동 미구현 — mock=True 사용")

    async def get_ohlcv(self, ticker: str, count: int = 100) -> pd.DataFrame:
        """OHLCV 데이터 조회."""
        if self.mock:
            return generate_ohlcv(ticker, count=count)
        # TODO: 실제 REST 구현
        raise NotImplementedError("실제 API 연동 미구현 — mock=True 사용")

    # ── 계좌 / 주문 ────────────────────────────────────────────────────────────

    async def get_balance(self) -> dict:
        """잔고 조회."""
        if self.mock:
            return {
                "cash": 9_500_000,
                "total_asset": 10_200_000,
                "positions": [
                    {"ticker": "005930", "qty": 10, "avg_price": 74500, "current_price": 75000},
                    {"ticker": "AAPL", "qty": 5, "avg_price": 185.50, "current_price": 191.00},
                    {"ticker": "NVDA", "qty": 2, "avg_price": 820.00, "current_price": 875.00},
                ],
            }
        # TODO: 실제 REST 구현
        raise NotImplementedError("실제 API 연동 미구현 — mock=True 사용")

    async def place_order(
        self,
        ticker: str,
        side: str,        # "BUY" | "SELL"
        price: int,
        qty: int,
        order_type: str = "LIMIT",  # "LIMIT" | "MARKET"
    ) -> dict:
        """주문 실행."""
        if self.mock:
            order_id = f"MOCK-{ticker}-{int(datetime.now().timestamp())}"
            logger.info(f"[MOCK 주문] {side} {ticker} {qty}주 @ {price:,}원  id={order_id}")
            return {
                "order_id": order_id,
                "status": "FILLED",
                "ticker": ticker,
                "side": side,
                "price": price,
                "qty": qty,
                "filled_qty": qty,
                "timestamp": datetime.now().isoformat(),
            }
        # TODO: 실제 REST 구현
        raise NotImplementedError("실제 API 연동 미구현 — mock=True 사용")

    async def cancel_order(self, order_id: str) -> bool:
        """주문 취소."""
        if self.mock:
            logger.info(f"[MOCK 취소] order_id={order_id}")
            return True
        # TODO: 실제 REST 구현
        raise NotImplementedError("실제 API 연동 미구현 — mock=True 사용")

    # ── WebSocket 실시간 시세 ──────────────────────────────────────────────────

    async def subscribe_realtime(
        self,
        tickers: list[str],
        callback: Callable[[str, dict], None],
        interval_seconds: int = 5,
    ) -> None:
        """
        실시간 시세를 구독합니다.
        Mock 모드에서는 interval_seconds마다 랜덤 시세를 생성합니다.

        Args:
            tickers: 구독할 종목 코드 리스트
            callback: 데이터 수신 시 호출할 함수 (ticker, tick_data)
            interval_seconds: Mock 업데이트 간격
        """
        if self.mock:
            logger.info(f"[MOCK WebSocket] {tickers} 구독 시작 (interval={interval_seconds}s)")
            prices = {t: generate_ohlcv(t, 1).iloc[-1]["close"] for t in tickers}

            def _round_price(t: str, p: float) -> float:
                return round(p, 2) if (t.isalpha() and t.isupper()) else round(p, -1)

            while True:
                for ticker in tickers:
                    change = random.gauss(0, 0.003)
                    prices[ticker] = max(prices[ticker] * (1 + change), 0.01)
                    tick = {
                        "ticker": ticker,
                        "price": _round_price(ticker, prices[ticker]),
                        "volume": random.randint(10_000, 500_000),
                        "timestamp": datetime.now().isoformat(),
                    }
                    await callback(ticker, tick)
                await asyncio.sleep(interval_seconds)
        else:
            # TODO: 실제 WebSocket 구현
            raise NotImplementedError("실제 API 연동 미구현 — mock=True 사용")
