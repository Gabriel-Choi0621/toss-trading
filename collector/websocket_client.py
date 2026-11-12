"""
Data Collector
실시간 시세를 수집하고 캔들 버퍼를 관리합니다.
"""
import asyncio
import logging
from collections import deque
from datetime import datetime
from typing import Callable, Optional

import pandas as pd

from collector.toss_api import TossAPIInterface
from config_loader import get

logger = logging.getLogger(__name__)


class DataCollector:
    """
    실시간 시세 수집기.

    - WebSocket(또는 Mock)으로 시세를 수신합니다.
    - 종목별로 OHLCV 버퍼를 관리합니다.
    - 버퍼가 candle_count를 채우면 callback을 호출합니다.
    """

    def __init__(
        self,
        tickers: list[str],
        on_candles: Callable[[str, pd.DataFrame], None],
        candle_count: Optional[int] = None,
        on_tick: Optional[Callable] = None,
    ):
        self.tickers = tickers
        self.on_candles = on_candles
        self.on_tick_cb = on_tick  # tick마다 호출 (손절/익절 모니터링용)
        self.candle_count = candle_count or get("trading.candle_count", 100)
        self.interval = get("trading.interval_seconds", 60)
        self.mock = get("toss.mock_mode", True)

        self.api = TossAPIInterface(
            app_key=get("toss.app_key", ""),
            app_secret=get("toss.app_secret", ""),
            mock=self.mock,
        )

        # 종목별 tick 버퍼 (deque로 오래된 데이터 자동 삭제)
        self._buffers: dict[str, deque] = {
            t: deque(maxlen=self.candle_count) for t in tickers
        }
        self._running = False

    async def _on_tick(self, ticker: str, tick: dict) -> None:
        """tick 데이터를 버퍼에 쌓고 가득 차면 콜백 호출."""
        # 손절/익절 모니터링 콜백 — 매 tick마다 호출
        if self.on_tick_cb:
            try:
                await self.on_tick_cb(ticker, tick)
            except Exception as e:
                logger.error(f"{ticker} on_tick 콜백 오류: {e}", exc_info=True)

        self._buffers[ticker].append(tick)
        buf_len = len(self._buffers[ticker])

        logger.debug(f"{ticker} tick 수신 | 버퍼 {buf_len}/{self.candle_count}")

        if buf_len >= self.candle_count:
            df = self._buffer_to_df(ticker)
            try:
                await self.on_candles(ticker, df)
            except Exception as e:
                logger.error(f"{ticker} 콜백 오류: {e}", exc_info=True)

    def _buffer_to_df(self, ticker: str) -> pd.DataFrame:
        """버퍼를 OHLCV DataFrame으로 변환합니다."""
        ticks = list(self._buffers[ticker])
        records = []
        for i, tick in enumerate(ticks):
            price = tick["price"]
            prev_price = ticks[i - 1]["price"] if i > 0 else price
            records.append({
                "open": prev_price,
                "high": max(prev_price, price),
                "low": min(prev_price, price),
                "close": price,
                "volume": tick["volume"],
            })
        df = pd.DataFrame(records)
        df.index = pd.RangeIndex(len(df))
        return df

    async def prefetch(self) -> None:
        """시작 시 기존 OHLCV 데이터를 미리 로드해 버퍼를 채웁니다."""
        logger.info("초기 OHLCV 데이터 로드 중...")
        for ticker in self.tickers:
            df = await self.api.get_ohlcv(ticker, count=self.candle_count)
            for _, row in df.iterrows():
                self._buffers[ticker].append({
                    "ticker": ticker,
                    "price": row["close"],
                    "volume": row["volume"],
                    "timestamp": str(row.name),
                })
            logger.info(f"{ticker} 초기 데이터 {len(df)}개 로드 완료")

    async def start(self) -> None:
        """수집을 시작합니다."""
        self._running = True
        logger.info(f"DataCollector 시작 | 종목: {self.tickers} | mock={self.mock}")

        await self.prefetch()

        try:
            await self.api.subscribe_realtime(
                tickers=self.tickers,
                callback=self._on_tick,
                interval_seconds=self.interval,
            )
        except asyncio.CancelledError:
            logger.info("DataCollector 종료")
        finally:
            self._running = False

    def stop(self) -> None:
        self._running = False
