"""
WebSocket 서버
Python 매매 시스템 ↔ Electron UI 브릿지

Electron이 ws://localhost:8765 에 연결합니다.
"""
import asyncio
import json
import logging
from typing import Set

logger = logging.getLogger(__name__)

try:
    import websockets
    _WS_AVAILABLE = True
except ImportError:
    _WS_AVAILABLE = False
    logger.warning("websockets 미설치. pip install websockets")


class UIServer:
    """
    Electron UI에 실시간 데이터를 전송하는 WebSocket 서버.
    main.py의 파이프라인에서 이벤트를 받아 Electron으로 브로드캐스트합니다.
    """

    def __init__(self, host: str = "localhost", port: int = 8765):
        self.host = host
        self.port = port
        self._clients: Set = set()
        self._command_callback = None  # async callable(cmd: dict)
        self._connect_callback = None  # async callable() — 클라이언트 연결 시 호출

    def on_command(self, callback) -> None:
        """Electron에서 오는 커맨드를 처리할 콜백 등록. callback(cmd: dict) -> None"""
        self._command_callback = callback

    def on_connect(self, callback) -> None:
        """Electron 클라이언트 연결 시 콜백 등록 (초기 config 전송 등)."""
        self._connect_callback = callback

    async def broadcast(self, msg: dict) -> None:
        """연결된 모든 Electron 클라이언트에 메시지 전송."""
        if not self._clients:
            return
        data = json.dumps(msg, ensure_ascii=False)
        disconnected = set()
        for ws in self._clients:
            try:
                await ws.send(data)
            except Exception:
                disconnected.add(ws)
        self._clients -= disconnected

    # ── 편의 메서드 ─────────────────────────────────────────────────────────────

    async def send_tick(self, ticker: str, price: int, volume: int) -> None:
        await self.broadcast({"type": "tick", "ticker": ticker, "price": price, "volume": volume})

    async def send_signal(self, ticker: str, signal: str, confidence: int, reason: str,
                          target_price: int = 0, stop_loss: int = 0) -> None:
        await self.broadcast({
            "type": "signal",
            "ticker": ticker,
            "signal": signal,
            "confidence": confidence,
            "reason": reason,
            "target_price": target_price,
            "stop_loss": stop_loss,
        })

    async def send_order(self, ticker: str, side: str, qty: int, price: int, pnl: int = 0) -> None:
        await self.broadcast({
            "type": "order",
            "ticker": ticker,
            "side": side,
            "qty": qty,
            "price": price,
            "pnl": pnl,
        })

    async def send_balance(self, total_asset: int, today_pnl: int, cash: int = 0, positions: list = None) -> None:
        await self.broadcast({
            "type": "balance",
            "total_asset": total_asset,
            "today_pnl": today_pnl,
            "cash": cash,
            "positions": positions or [],
        })

    async def send_config(self, auto_trade: bool, dry_run: bool, mock_mode: bool = True,
                          market: str = "KR", tickers: list = None,
                          use_ai: bool = True, min_interval_minutes: float = 5.0) -> None:
        await self.broadcast({
            "type": "config",
            "auto_trade": auto_trade,
            "dry_run": dry_run,
            "mock_mode": mock_mode,
            "market": market,
            "tickers": tickers or [],
            "use_ai": use_ai,
            "min_interval_minutes": min_interval_minutes,
        })

    async def send_candles(self, ticker: str, ohlcv: list, indicators: dict = None) -> None:
        """ohlcv: [{"t": epoch_ms, "o": int, "h": int, "l": int, "c": int, "v": int}, ...]"""
        await self.broadcast({
            "type": "candles",
            "ticker": ticker,
            "ohlcv": ohlcv,
            "indicators": indicators or {},
        })

    async def send_notification(self, message: str, ntype: str = "info", duration: int = 0) -> None:
        """UI 토스트 알림 전송. ntype: success | error | warn | info
        duration: 표시 시간(ms), 0이면 기본값 사용
        """
        payload: dict = {"type": "notification", "message": message, "ntype": ntype}
        if duration:
            payload["duration"] = duration
        await self.broadcast(payload)

    async def send_ai_thinking(self, ticker: str) -> None:
        """AI 판단 시작 알림 (UI 판단 중 표시용)."""
        await self.broadcast({"type": "ai_thinking", "ticker": ticker})

    async def send_ai_status(self, remaining: int, daily: int, limit: int,
                              last_ticker: str = "", last_time: str = "") -> None:
        """Gemini 일일 호출 현황 전송."""
        await self.broadcast({
            "type": "ai_status", "remaining": remaining, "daily": daily, "limit": limit,
            "last_ticker": last_ticker, "last_time": last_time,
        })

    async def send_orders_history(self, orders: list) -> None:
        """거래 내역 (DB 기반) 전송."""
        await self.broadcast({"type": "orders_history", "orders": orders})

    async def send_backtest_result(self, ticker: str, result: dict) -> None:
        """백테스트 결과 전송."""
        await self.broadcast({"type": "backtest_result", "ticker": ticker, **result})

    async def send_price_alert(self, ticker: str, price: float, direction: str) -> None:
        """가격 알람 발동 알림."""
        await self.broadcast({"type": "price_alert", "ticker": ticker, "price": price, "direction": direction})

    async def send_rebalance_result(self, actions: list) -> None:
        """리밸런싱 결과 전송."""
        await self.broadcast({"type": "rebalance_result", "actions": actions})

    async def send_performance(self, performance: dict) -> None:
        """성과 지표 전송 (승률, MDD, 샤프지수, 일별 손익 등)."""
        await self.broadcast({"type": "performance", **performance})

    # ── 서버 실행 ────────────────────────────────────────────────────────────────
    async def _handler(self, websocket) -> None:
        self._clients.add(websocket)
        logger.info(f"Electron connected ({len(self._clients)} total)")
        try:
            if self._connect_callback:
                await self._connect_callback()
            async for raw in websocket:
                try:
                    cmd = json.loads(raw)
                    logger.debug(f"Command from Electron: {cmd}")
                    if self._command_callback:
                        await self._command_callback(cmd)
                except Exception as e:
                    logger.warning(f"Command parse error: {e}")
        except Exception:
            pass
        finally:
            self._clients.discard(websocket)
            logger.info(f"Electron disconnected ({len(self._clients)} total)")

    async def start(self) -> None:
        if not _WS_AVAILABLE:
            logger.error("websockets 패키지가 없어 UI 서버를 시작할 수 없습니다.")
            return
        import websockets as ws_lib
        async with ws_lib.serve(self._handler, self.host, self.port):
            logger.info(f"UI 서버 시작 ws://{self.host}:{self.port}")
            await asyncio.Future()  # 무한 대기