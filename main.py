"""
자동매매 시스템 메인 오케스트레이터
모든 에이전트를 초기화하고 파이프라인을 실행합니다.

실행 방법:
  python main.py               # dry-run (기본)
  python main.py --live        # 실전 (주의!)
  python main.py --once        # 현재 시그널만 확인하고 종료
"""
import argparse
import asyncio
import logging
import sys
from datetime import datetime, timedelta

from config_loader import load_config, setup_logging, get, set as cfg_set, save_config
from analyzer.technical import analyze, analyze_daily
from ai.judgment_engine import JudgmentEngine, JudgmentResult
from executor.order import OrderExecutor
from ui_server import UIServer

logger = logging.getLogger(__name__)


class TradingSystem:
    """자동매매 시스템 메인 클래스."""

    def __init__(self, dry_run: bool = True):
        self.dry_run = dry_run
        self.kr_tickers: list[str] = get("trading.tickers", ["005930"])
        self.us_tickers: list[str] = get("trading.us_tickers", ["AAPL", "TSLA", "NVDA"])
        self.market: str = "KR"
        self.tickers: list[str] = self.kr_tickers
        self.confidence_auto: int = get("trading.confidence.auto_execute", 80)
        self.confidence_ask: int = get("trading.confidence.ask_human", 60)
        self.min_interval_minutes: float = get("gemini.min_interval_minutes", 5)

        # 장 중이면 ON, 장 외면 OFF로 초기화 (watcher 10초 딜레이 없이 처음부터 올바른 상태)
        _market_open = self._is_market_open()
        self.auto_trade: bool = _market_open
        self.use_ai: bool = _market_open
        self.collector = None
        self._collector_task = None

        # 손절가·목표가 추적: {ticker: {"stop_loss": float, "target_price": float}}
        self._position_limits: dict[str, dict] = {}
        # 이미 매도 트리거된 종목 (중복 실행 방지)
        self._sell_triggered: set[str] = set()
        # 직전 AI 판단 추적: {ticker: {"signal": str, "confidence": int}}
        self._last_signals: dict[str, dict] = {}
        # 트레일링 스탑: {ticker: 고점(float)} — 포지션 보유 중에만 유지
        self._trailing_highs: dict[str, float] = {}
        # 트레일링 스탑 낙폭 비율 (기본 3%)
        self._trailing_stop_pct: float = get("trading.trailing_stop_pct", 0.03)
        # 부분 익절 비율 (기본 50% — 0이면 전량 매도)
        self._partial_take_profit: float = get("trading.partial_take_profit", 0.5)
        # 일봉 지표 캐시: {ticker: {"indicators": dict, "fetched_at": datetime}}
        self._daily_cache: dict[str, dict] = {}
        # 일봉 갱신 주기 (기본 30분)
        self._daily_refresh_minutes: int = get("trading.daily_refresh_minutes", 30)
        # 가격 알람: {ticker: {price, direction, triggered}}
        self._price_alerts: dict[str, dict] = {}
        # AI 마지막 판단 시각: {ticker: datetime}
        self._last_judgment_at: dict[str, datetime] = {}

        self.judge = JudgmentEngine()
        self.executor = OrderExecutor(dry_run=dry_run)
        self.ui = UIServer()
        self.ui.on_command(self._on_command)
        self.ui.on_connect(self._on_connect)
        self._running = False

    # ── 컬렉터 관리 ───────────────────────────────────────────────────────────

    def _make_collector(self):
        from collector.websocket_client import DataCollector
        return DataCollector(
            tickers=self.tickers,
            on_candles=self._on_candles,
            on_tick=self._on_tick_price,
        )

    async def _restart_collector(self) -> None:
        """컬렉터를 재시작합니다 (시장 전환 시)."""
        if self._collector_task and not self._collector_task.done():
            self._collector_task.cancel()
            try:
                await self._collector_task
            except asyncio.CancelledError:
                pass
        # 시장 전환 시 상태 초기화
        self._position_limits.clear()
        self._sell_triggered.clear()
        self._last_signals.clear()
        self._trailing_highs.clear()
        self._daily_cache.clear()
        self.collector = self._make_collector()
        self._collector_task = asyncio.create_task(self.collector.start())

    # ── 파이프라인 ─────────────────────────────────────────────────────────────

    async def _on_candles(self, ticker: str, df) -> None:
        """캔들 수신 → 분석 → AI 판단 → 실행 파이프라인."""
        # 1. 기술적 지표 계산
        indicators = analyze(df)
        if indicators is None:
            return

        # 2. AI 판단 — use_ai=False면 스킵
        if not self.use_ai:
            logger.debug(f"[{ticker}] AI 판단 비활성화 — 스킵")
            return

        # 포지션 context 조회
        try:
            balance = await self.executor.api.get_balance()
            positions = {p["ticker"]: p for p in balance.get("positions", [])}
            position = positions.get(ticker)
        except Exception:
            position = None

        # 일봉 지표 조회 (30분 캐시)
        daily_indicators = None
        cache = self._daily_cache.get(ticker)
        now = datetime.now()
        if (
            cache is None or
            now - cache["fetched_at"] > timedelta(minutes=self._daily_refresh_minutes)
        ):
            try:
                daily_df = await self.executor.api.get_ohlcv(ticker, count=120)
                daily_indicators = analyze_daily(daily_df)
                self._daily_cache[ticker] = {"indicators": daily_indicators, "fetched_at": now}
                logger.debug(f"[{ticker}] 일봉 지표 갱신 완료")
            except Exception as e:
                logger.warning(f"[{ticker}] 일봉 데이터 조회 실패: {e}")
                daily_indicators = cache["indicators"] if cache else None
        else:
            daily_indicators = cache["indicators"]

        # 직전 AI 판단 context
        prev = self._last_signals.get(ticker, {})

        # await 이후 재확인 — 비동기 대기 중 use_ai가 꺼졌을 수 있음
        if not self.use_ai:
            logger.debug(f"[{ticker}] AI 비활성 (대기 중 변경) — 스킵")
            return

        # AI 판단 시작 알림 (UI에서 "판단 중" 표시)
        await self.ui.send_ai_thinking(ticker)

        judgment = await self.judge.judge(
            ticker, indicators,
            position=position,
            prev_signal=prev.get("signal"),
            prev_confidence=prev.get("confidence"),
            daily_indicators=daily_indicators,
        )
        self._last_judgment_at[ticker] = datetime.now()
        logger.info(f"판단 결과: {judgment}")

        # 직전 신호 갱신 + DB 영속 저장 (재시작 후 연속성 보장)
        self._last_signals[ticker] = {"signal": judgment.signal, "confidence": judgment.confidence}
        self.executor.save_last_signal(ticker, judgment.signal, judgment.confidence, judgment.reason)

        # 손절가·목표가 업데이트 및 DB 저장
        if judgment.signal == "BUY" and (judgment.stop_loss > 0 or judgment.target_price > 0):
            self._position_limits[ticker] = {
                "stop_loss": judgment.stop_loss,
                "target_price": judgment.target_price,
            }
            self.executor.save_position_limit(ticker, judgment.stop_loss, judgment.target_price)
            logger.debug(
                f"[{ticker}] 포지션 한도 설정 — 손절 {judgment.stop_loss:,} / 목표 {judgment.target_price:,}"
            )
            # 트레일링 스탑 고점 초기화 (BUY 시 현재가 기준)
            cur_price = indicators.get("current_price", 0)
            if cur_price > 0:
                prev_high = self._trailing_highs.get(ticker, 0)
                self._trailing_highs[ticker] = max(float(cur_price), prev_high)
                logger.debug(f"[{ticker}] 트레일링 스탑 고점 초기화: {self._trailing_highs[ticker]:,}")
        elif judgment.signal == "SELL":
            self._position_limits.pop(ticker, None)
            self.executor.delete_position_limit(ticker)
            self._trailing_highs.pop(ticker, None)

        # UI로 신호 전송
        await self.ui.send_signal(
            ticker=ticker,
            signal=judgment.signal,
            confidence=judgment.confidence,
            reason=judgment.reason,
            target_price=judgment.target_price,
            stop_loss=judgment.stop_loss,
        )

        # Gemini 호출 현황 전송 (판단 완료 → "대기 중" 상태로 전환)
        s = self.judge.rate_limit_status
        last_at = self._last_judgment_at.get(ticker)
        await self.ui.send_ai_status(
            remaining=s["remaining"],
            daily=s["daily"],
            limit=s["daily_limit"],
            last_ticker=ticker,
            last_time=last_at.isoformat() if last_at else "",
        )

        # 3. 주문 실행은 장 시간에만
        if not self._is_market_open():
            logger.debug("장 시간 외 — 주문 실행 생략")
            return

        if self.auto_trade and judgment.confidence >= self.confidence_auto:
            logger.info(f"[auto execute] confidence {judgment.confidence}%")
            result = await self.executor.execute(judgment)
            logger.info(f"order result: {result}")
            if result.get("status") == "rejected":
                await self.ui.send_notification(f"주문 거부: {result.get('reason', '')}", "error")
            elif result.get("status") not in ("skipped",):
                if judgment.signal == "SELL":
                    self._position_limits.pop(ticker, None)  # 매도 완료 → 한도 해제
                    self._trailing_highs.pop(ticker, None)   # 트레일링 고점 해제
                await self._send_balance()
                await self.ui.send_notification(
                    f"{judgment.signal} {ticker} {result.get('qty', 0)}주 @ {result.get('price', 0):,}원",
                    "success",
                )

        elif judgment.confidence >= self.confidence_ask:
            # TODO: 텔레그램 Human-in-the-loop 구현 후 연동
            logger.info(
                f"[텔레그램 확인 필요] 신뢰도 {judgment.confidence}% | {judgment.reason}"
            )

        else:
            logger.info(f"[무시] 신뢰도 부족 ({judgment.confidence}%)")

    async def _on_tick_price(self, ticker: str, tick: dict) -> None:
        """매 tick마다 UI 가격 업데이트 + 손절가·목표가 도달 여부를 확인합니다."""
        # UI 가격 업데이트는 장 외에도 항상 전송
        await self.ui.send_tick(
            ticker=ticker,
            price=tick["price"],
            volume=tick.get("volume", 0),
        )

        # 장 외 시간에는 자동 매매 실행 없음
        if not self._is_market_open():
            return

        if ticker in self._sell_triggered:
            return

        price = float(tick["price"])

        # ── 가격 알람 체크 ────────────────────────────────────────────────────
        alert = self._price_alerts.get(ticker)
        if alert and not alert.get("triggered", False):
            alert_price = alert["price"]
            direction = alert["direction"]
            triggered = (
                (direction == "above" and price >= alert_price) or
                (direction == "below" and price <= alert_price)
            )
            if triggered:
                alert["triggered"] = True
                arrow = "↑" if direction == "above" else "↓"
                label = f"⏰ {ticker} 가격 알람: {arrow} {alert_price:,} (현재 {price:,})"
                logger.info(label)
                await self.ui.send_notification(label, "warn")
                await self.ui.send_price_alert(ticker, price, direction)

        # ── 트레일링 스탑: 고점 갱신 + 낙폭 체크 ─────────────────────────────
        if ticker in self._trailing_highs:
            if price > self._trailing_highs[ticker]:
                self._trailing_highs[ticker] = price  # 신고점 갱신
            else:
                high = self._trailing_highs[ticker]
                drawdown = (high - price) / high if high > 0 else 0
                if drawdown >= self._trailing_stop_pct:
                    self._sell_triggered.add(ticker)
                    label = (
                        f"📉 {ticker} 트레일링 스탑 발동 "
                        f"(고점 {high:,} → 현재 {price:,}, "
                        f"낙폭 {drawdown:.1%}) — 자동 매도"
                    )
                    logger.warning(label)
                    try:
                        await self.ui.send_notification(label, "error")
                        result = await self.executor.execute_stop_loss(ticker, price)
                        logger.info(f"[{ticker}] 트레일링 스탑 매도 결과: {result}")
                        if result.get("status") not in ("skipped", "rejected"):
                            self._position_limits.pop(ticker, None)
                            self._trailing_highs.pop(ticker, None)
                            self.executor.delete_position_limit(ticker)
                            await self.ui.send_order(
                                ticker=ticker, side="SELL",
                                qty=result.get("qty", 0),
                                price=result.get("price", price),
                                pnl=0,
                            )
                            await self._send_balance()
                    finally:
                        self._sell_triggered.discard(ticker)
                    return

        limits = self._position_limits.get(ticker)
        if not limits:
            return

        stop_loss = limits.get("stop_loss", 0)
        target_price = limits.get("target_price", 0)

        if stop_loss > 0 and price <= stop_loss:
            # ── 고정 손절가 ───────────────────────────────────────────────────
            self._sell_triggered.add(ticker)
            label = f"⚠️ {ticker} 손절가 {stop_loss:,} 도달 (현재 {price:,}) — 자동 매도"
            logger.warning(label)
            try:
                await self.ui.send_notification(label, "error")
                result = await self.executor.execute_stop_loss(ticker, price)
                logger.warning(f"[{ticker}] 손절 결과: {result}")
                if result.get("status") not in ("skipped", "rejected"):
                    self._position_limits.pop(ticker, None)
                    self._trailing_highs.pop(ticker, None)
                    self.executor.delete_position_limit(ticker)
                    await self.ui.send_order(
                        ticker=ticker, side="SELL",
                        qty=result.get("qty", 0), price=result.get("price", price), pnl=0,
                    )
                    await self._send_balance()
                else:
                    await self.ui.send_notification(f"손절 매도 거부: {result.get('reason', '')}", "warn")
            finally:
                self._sell_triggered.discard(ticker)

        elif target_price > 0 and price >= target_price:
            # ── 목표가 도달 (부분 or 전량 익절) ─────────────────────────────
            self._sell_triggered.add(ticker)
            try:
                partial_ratio = self._partial_take_profit  # 0.0이면 전량
                partial_qty = 0

                pos = None  # 명시적 초기화 — UnboundLocalError 방지
                if 0 < partial_ratio < 1.0:
                    # 현재 보유 수량 조회
                    try:
                        balance = await self.executor.api.get_balance()
                        positions = {p["ticker"]: p for p in balance.get("positions", [])}
                        pos = positions.get(ticker)
                        if pos and pos["qty"] > 1:
                            partial_qty = max(1, int(pos["qty"] * partial_ratio))
                    except Exception as e:
                        logger.warning(f"[{ticker}] 부분 익절 잔고 조회 실패 → 전량 매도로 폴백: {e}")

                is_partial = 0 < partial_qty < (pos["qty"] if pos else 0)
                label = (
                    f"🎯 {ticker} 목표가 {target_price:,} 도달 (현재 {price:,}) — "
                    f"{'부분 ' + str(partial_qty) + '주 익절' if is_partial else '전량 익절'}"
                )
                logger.info(label)
                await self.ui.send_notification(label, "success")

                result = await self.executor.execute_take_profit(ticker, price, partial_qty=partial_qty)
                logger.info(f"[{ticker}] 익절 결과: {result}")

                if result.get("status") not in ("skipped", "rejected"):
                    await self.ui.send_order(
                        ticker=ticker, side="SELL",
                        qty=result.get("qty", 0), price=result.get("price", price), pnl=0,
                    )
                    await self._send_balance()

                    if is_partial:
                        # 부분 익절 성공 → 2차 목표가를 5% 높게 설정하고 계속 모니터링
                        new_target = round(target_price * 1.05, 2)
                        self._position_limits[ticker]["target_price"] = new_target
                        self.executor.save_position_limit(
                            ticker,
                            stop_loss=self._position_limits[ticker].get("stop_loss", 0),
                            target_price=new_target,
                        )
                        await self.ui.send_notification(
                            f"📈 {ticker} 2차 목표가 {new_target:,} 설정 — 나머지 포지션 계속 보유",
                            "success",
                        )
                        logger.info(f"[{ticker}] 2차 목표가 설정: {new_target:,}")
                    else:
                        # 전량 매도 완료
                        self._position_limits.pop(ticker, None)
                        self._trailing_highs.pop(ticker, None)
                        self.executor.delete_position_limit(ticker)
                else:
                    await self.ui.send_notification(f"익절 매도 거부: {result.get('reason', '')}", "warn")
            finally:
                self._sell_triggered.discard(ticker)

    async def _on_connect(self) -> None:
        """Electron 연결 시 config + 잔고 + 거래내역 전송."""
        mock_mode = get("toss.mock_mode", True)
        await self.ui.send_config(self.auto_trade, self.dry_run, mock_mode, self.market, self.tickers, self.use_ai, self.min_interval_minutes)
        await self._send_balance()
        # 거래 내역 전송
        orders = self.executor.get_orders_history(50)
        await self.ui.send_orders_history(orders)
        # AI 현황 전송
        s = self.judge.rate_limit_status
        await self.ui.send_ai_status(
            remaining=s["remaining"], daily=s["daily"], limit=s["daily_limit"]
        )

    async def _send_balance(self) -> None:
        """잔고를 조회해서 UI로 전송."""
        try:
            balance = await self.executor.api.get_balance()
            today_pnl = self.executor.get_today_pnl()
            await self.ui.send_balance(
                total_asset=balance["total_asset"],
                today_pnl=today_pnl,
                cash=balance["cash"],
                positions=balance.get("positions", []),
            )
            # 성과 지표도 함께 전송
            perf = self.executor.get_performance(days=30)
            if perf:
                await self.ui.send_performance(perf)
        except Exception as e:
            logger.error(f"Balance fetch error: {e}")

    async def _on_command(self, cmd: dict) -> None:
        """Electron에서 오는 커맨드 처리."""
        cmd_type = cmd.get("type")

        mock_mode = get("toss.mock_mode", True)

        if cmd_type == "set_auto_trade":
            self.auto_trade = bool(cmd.get("value", True))
            if self.auto_trade and not self.use_ai:
                self.use_ai = True   # 자동매매 ON → AI 강제 ON
                logger.info("auto_trade ON → use_ai 강제 ON")
            logger.info(f"auto_trade -> {self.auto_trade}")
            await self.ui.send_config(self.auto_trade, self.dry_run, mock_mode, self.market, self.tickers, self.use_ai, self.min_interval_minutes)

        elif cmd_type == "set_use_ai":
            new_val = bool(cmd.get("value", True))
            if not new_val and self.auto_trade:
                # AI를 끄면 자동매매도 함께 OFF
                self.auto_trade = False
                logger.info("use_ai OFF → auto_trade 자동 OFF")
            self.use_ai = new_val
            logger.info(f"use_ai -> {self.use_ai}")
            await self.ui.send_config(self.auto_trade, self.dry_run, mock_mode, self.market, self.tickers, self.use_ai, self.min_interval_minutes)

        elif cmd_type == "set_dry_run":
            self.dry_run = bool(cmd.get("value", True))
            self.executor.dry_run = self.dry_run
            mode = "DRY-RUN" if self.dry_run else "LIVE"
            logger.info(f"dry_run -> {self.dry_run} [{mode}]")
            await self.ui.send_config(self.auto_trade, self.dry_run, mock_mode, self.market, self.tickers, self.use_ai, self.min_interval_minutes)

        elif cmd_type == "set_market":
            new_market = cmd.get("value", "KR")
            if new_market not in ("KR", "US"):
                return
            self.market = new_market
            self.tickers = self.kr_tickers if self.market == "KR" else self.us_tickers
            self.judge.set_market(self.market)
            self.judge.n_tickers = len(self.tickers)

            # 전환한 시장의 장 시간 여부에 따라 AI·자동매매 상태 조정
            market_name = "국내" if self.market == "KR" else "해외"
            is_open = self._is_market_open()
            if not is_open:
                changed = self.use_ai or self.auto_trade
                self.use_ai = False
                self.auto_trade = False
                if changed:
                    logger.info(f"시장 전환 → {market_name} 장 외 시간 — AI·자동매매 OFF")
                    await self.ui.send_notification(
                        f"🔔 {market_name} 장 마감 시간입니다. 자동매매를 종료합니다.",
                        "warn",
                        duration=5000,
                    )
            else:
                # 장 중인 시장으로 전환 시 AI만 ON (자동매매는 사용자가 직접 켜게)
                if not self.use_ai:
                    self.use_ai = True
                    logger.info(f"시장 전환 → {market_name} 장 중 — AI 자동 ON")
                    await self.ui.send_notification(
                        f"🤖 {market_name} 장 중입니다. AI 판단을 시작합니다.",
                        "success",
                        duration=4000,
                    )

            logger.info(f"market -> {self.market} | tickers: {self.tickers}")
            await self.ui.send_config(self.auto_trade, self.dry_run, mock_mode, self.market, self.tickers, self.use_ai, self.min_interval_minutes)
            await self._restart_collector()

        elif cmd_type == "manual_order":
            ticker = cmd.get("ticker", "")
            side   = cmd.get("side", "BUY")
            qty    = int(cmd.get("qty", 0))
            is_market = bool(cmd.get("market_order", False))
            # US 주식은 소수점 가격이므로 float 유지
            price  = round(float(cmd.get("price", 0)), 2)
            # 시장가 주문: 현재가를 즉시 조회
            if is_market or not price:
                try:
                    info = await self.executor.api.get_current_price(ticker)
                    price = round(float(info["price"]), 2)
                except Exception as e:
                    await self.ui.send_notification(f"현재가 조회 실패: {e}", "error")
                    return
            if not ticker or not price:
                return
            j = JudgmentResult(
                ticker=ticker,
                signal=side,
                confidence=100,
                target_price=price,
                stop_loss=0,
                target_return_pct=0.0,
                stop_loss_pct=0.0,
                reason="manual order from UI",
                risk_level="low",
                manual_qty=qty,
            )
            result = await self.executor.execute(j)
            logger.info(f"manual order result: {result}")
            if result.get("status") == "rejected":
                await self.ui.send_notification(f"주문 거부: {result.get('reason', '')}", "error")
                return
            if result.get("status") == "skipped":
                await self.ui.send_notification(f"주문 건너뜀: {result.get('reason', '')}", "warn")
                return
            await self.ui.send_order(
                ticker=ticker,
                side=side,
                qty=result.get("qty", 0),
                price=result.get("price", price),
                pnl=0,
            )
            await self.ui.send_notification(
                f"{side} {ticker} {result.get('qty', 0)}주 @ {result.get('price', price):,}원",
                "success",
            )
            await self._send_balance()

        elif cmd_type == "get_candles":
            ticker = cmd.get("ticker", "")
            if not ticker or not self.collector:
                return
            buf = self.collector._buffers.get(ticker)
            if not buf or len(buf) < 2:
                return
            df = self.collector._buffer_to_df(ticker)
            ohlcv = [
                {"o": round(float(r.open), 2), "h": round(float(r.high), 2),
                 "l": round(float(r.low), 2),  "c": round(float(r.close), 2),
                 "v": int(r.volume)}
                for _, r in df.iterrows()
            ]
            indicators = analyze(df) or {}
            await self.ui.send_candles(ticker, ohlcv, indicators)

        elif cmd_type == "add_ticker":
            # ── 종목 추가 ─────────────────────────────────────────────────────
            ticker = cmd.get("ticker", "").strip().upper()
            if not ticker:
                return
            target_list = self.us_tickers if self.market == "US" else self.kr_tickers
            if ticker in target_list:
                await self.ui.send_notification(f"{ticker} 이미 등록된 종목입니다", "warn")
                return
            target_list.append(ticker)
            self.tickers = target_list
            cfg_key = "trading.us_tickers" if self.market == "US" else "trading.tickers"
            cfg_set(cfg_key, target_list)
            save_config()
            logger.info(f"종목 추가: {ticker} → {self.tickers}")
            await self.ui.send_config(self.auto_trade, self.dry_run, mock_mode, self.market, self.tickers, self.use_ai, self.min_interval_minutes)
            await self.ui.send_notification(f"✅ {ticker} 종목 추가됨", "success")
            await self._restart_collector()

        elif cmd_type == "remove_ticker":
            # ── 종목 제거 ─────────────────────────────────────────────────────
            ticker = cmd.get("ticker", "").strip().upper()
            target_list = self.us_tickers if self.market == "US" else self.kr_tickers
            if ticker not in target_list:
                await self.ui.send_notification(f"{ticker} 목록에 없는 종목입니다", "warn")
                return
            if len(target_list) <= 1:
                await self.ui.send_notification("최소 1개 종목은 유지해야 합니다", "warn")
                return
            target_list.remove(ticker)
            self.tickers = target_list
            cfg_key = "trading.us_tickers" if self.market == "US" else "trading.tickers"
            cfg_set(cfg_key, target_list)
            save_config()
            logger.info(f"종목 제거: {ticker} → {self.tickers}")
            await self.ui.send_config(self.auto_trade, self.dry_run, mock_mode, self.market, self.tickers, self.use_ai, self.min_interval_minutes)
            await self.ui.send_notification(f"🗑️ {ticker} 종목 제거됨", "success")
            await self._restart_collector()

        elif cmd_type == "update_config":
            # ── 설정 파라미터 변경 ─────────────────────────────────────────────
            # 허용된 키만 변경 (보안상 임의 키 변경 차단)
            ALLOWED_KEYS = {
                "trading.risk.max_position_ratio",
                "trading.risk.daily_loss_limit",
                "trading.risk.consecutive_stop_limit",
                "trading.risk.cooldown_minutes",
                "trading.trailing_stop_pct",
                "trading.partial_take_profit",
                "trading.daily_refresh_minutes",
                "trading.confidence.auto_execute",
                "trading.confidence.ask_human",
                "gemini.daily_limit",
                "gemini.rpm_limit",
                "gemini.model",
            }
            updates = cmd.get("updates", {})
            changed = []
            for key, value in updates.items():
                if key not in ALLOWED_KEYS:
                    logger.warning(f"update_config: 허용되지 않은 키 무시 — {key}")
                    continue
                cfg_set(key, value)
                changed.append(f"{key}={value}")
                # 런타임 즉시 반영
                if key == "trading.trailing_stop_pct":
                    self._trailing_stop_pct = float(value)
                elif key == "trading.partial_take_profit":
                    self._partial_take_profit = float(value)
                elif key == "trading.daily_refresh_minutes":
                    self._daily_refresh_minutes = int(value)
                elif key == "trading.confidence.auto_execute":
                    self.confidence_auto = int(value)
                elif key == "trading.confidence.ask_human":
                    self.confidence_ask = int(value)
                elif key.startswith("trading.risk."):
                    # 리스크 파라미터는 RiskManager 직접 업데이트
                    rm = self.executor.risk
                    if key == "trading.risk.max_position_ratio":
                        rm.max_position_ratio = float(value)
                    elif key == "trading.risk.daily_loss_limit":
                        rm.daily_loss_limit = float(value)
                    elif key == "trading.risk.consecutive_stop_limit":
                        rm.consecutive_stop_limit = int(value)
                    elif key == "trading.risk.cooldown_minutes":
                        rm.cooldown_minutes = int(value)

            if changed:
                save_config()
                logger.info(f"설정 변경: {', '.join(changed)}")
                await self.ui.send_notification(f"⚙️ 설정 저장됨: {len(changed)}개 항목", "success")

        elif cmd_type == "get_orders":
            # ── 거래 내역 조회 (DB) ───────────────────────────────────────────
            limit = int(cmd.get("limit", 50))
            orders = self.executor.get_orders_history(limit)
            await self.ui.send_orders_history(orders)

        elif cmd_type == "cancel_order":
            # ── 주문 취소 ─────────────────────────────────────────────────────
            order_id = cmd.get("order_id", "")
            if order_id:
                ok = self.executor.cancel_order(order_id)
                if ok:
                    await self.ui.send_notification(f"🚫 주문 취소: {order_id[:20]}", "info")
                    orders = self.executor.get_orders_history(50)
                    await self.ui.send_orders_history(orders)

        elif cmd_type == "run_backtest":
            # ── 백테스트 실행 ─────────────────────────────────────────────────
            ticker = cmd.get("ticker", "")
            if not ticker or not self.collector:
                await self.ui.send_notification("백테스트: 종목 또는 데이터 없음", "warn")
                return
            buf = self.collector._buffers.get(ticker)
            if not buf or len(buf) < 10:
                await self.ui.send_notification(f"{ticker} 백테스트 데이터 부족 (최소 10개)", "warn")
                return
            try:
                from backtest.engine import BacktestEngine
                df = self.collector._buffer_to_df(ticker)
                engine = BacktestEngine()
                result = engine.run(df, ticker)
                await self.ui.send_backtest_result(ticker, {
                    "total_trades": result.total_trades,
                    "win_trades": result.win_trades,
                    "loss_trades": result.loss_trades,
                    "win_rate": result.win_rate,
                    "total_pnl": result.total_return_pct,
                    "profit_factor": result.profit_factor,
                    "mdd": result.max_drawdown_pct,
                    "sharpe": result.sharpe_ratio,
                    "avg_profit_pct": result.avg_profit_pct,
                    "avg_loss_pct": result.avg_loss_pct,
                })
                logger.info(f"백테스트 완료: {result}")
            except Exception as e:
                logger.error(f"백테스트 오류: {e}", exc_info=True)
                await self.ui.send_notification(f"백테스트 실패: {e}", "error")

        elif cmd_type == "set_price_alert":
            # ── 가격 알람 설정 ────────────────────────────────────────────────
            ticker = cmd.get("ticker", "")
            price = round(float(cmd.get("price", 0)), 2)
            direction = cmd.get("direction", "above")  # "above" | "below"
            if ticker and price > 0:
                self._price_alerts[ticker] = {
                    "price": price, "direction": direction, "triggered": False
                }
                arrow = "↑" if direction == "above" else "↓"
                await self.ui.send_notification(
                    f"⏰ {ticker} 알람 설정: {arrow} {price:,}", "info"
                )
                logger.info(f"가격 알람 설정: {ticker} {direction} {price:,}")

        elif cmd_type == "clear_price_alert":
            # ── 가격 알람 해제 ────────────────────────────────────────────────
            ticker = cmd.get("ticker", "")
            if ticker and ticker in self._price_alerts:
                self._price_alerts.pop(ticker)
                await self.ui.send_notification(f"⏰ {ticker} 알람 해제", "info")

        elif cmd_type == "set_gemini_model":
            # ── Gemini 모델 런타임 전환 ──────────────────────────────────────
            model = cmd.get("model", "gemini-2.5-flash")
            ALLOWED_MODELS = {
                "gemini-2.5-flash-lite",
                "gemini-2.5-flash",
                "gemini-2.5-pro",
            }
            if model not in ALLOWED_MODELS:
                await self.ui.send_notification(f"허용되지 않은 모델: {model}", "error")
                return
            self.judge.set_model(model)
            cfg_set("gemini.model", model)
            save_config()
            await self.ui.send_notification(f"🤖 Gemini 모델 → {model}", "success")

        elif cmd_type == "update_position_limits":
            # ── 손절가·목표가 수동 수정 ──────────────────────────────────────
            ticker = cmd.get("ticker", "")
            stop_loss = round(float(cmd.get("stop_loss", 0)), 2)
            target_price = round(float(cmd.get("target_price", 0)), 2)
            if not ticker:
                return
            current = self._position_limits.get(ticker, {})
            new_limits = {
                "stop_loss": stop_loss if stop_loss > 0 else current.get("stop_loss", 0),
                "target_price": target_price if target_price > 0 else current.get("target_price", 0),
            }
            self._position_limits[ticker] = new_limits
            self.executor.save_position_limit(
                ticker, new_limits["stop_loss"], new_limits["target_price"]
            )
            logger.info(f"[{ticker}] 포지션 한도 수동 수정: {new_limits}")
            await self.ui.send_notification(
                f"✅ {ticker} 손절 {new_limits['stop_loss']:,} / 목표 {new_limits['target_price']:,} 저장됨",
                "success",
            )

        elif cmd_type == "rebalance":
            # ── 포트폴리오 리밸런싱 ──────────────────────────────────────────
            await self._rebalance()

        elif cmd_type == "pre_market_check":
            # ── 장 전 준비 수동 트리거 ────────────────────────────────────────
            await self._pre_market_prepare()

    # ── 포트폴리오 리밸런싱 ─────────────────────────────────────────────────────

    async def _rebalance(self) -> None:
        """보유 종목을 동일 비중으로 리밸런싱합니다."""
        if not self.tickers:
            return
        try:
            balance = await self.executor.api.get_balance()
            total = balance["total_asset"]
            positions = {p["ticker"]: p for p in balance.get("positions", [])}
            target_ratio = 1.0 / len(self.tickers)
            target_value = total * target_ratio * 0.90  # 10% 여유분

            actions = []
            for ticker in self.tickers:
                pos = positions.get(ticker)
                if not pos:
                    continue
                current_value = pos["qty"] * pos["current_price"]
                diff = target_value - current_value
                price = pos["current_price"]
                if price <= 0:
                    continue
                qty = abs(int(diff / price))
                # 2% 미만 편차 무시 (과매매 방지)
                if qty <= 0 or abs(diff) < total * 0.02:
                    continue
                side = "BUY" if diff > 0 else "SELL"
                actions.append(f"{ticker} {side} {qty}주")
                j = JudgmentResult(
                    ticker=ticker, signal=side, confidence=100,
                    target_price=round(float(price), 2), stop_loss=0.0,
                    target_return_pct=0.0, stop_loss_pct=0.0,
                    reason="포트폴리오 리밸런싱", risk_level="low",
                    manual_qty=qty,
                )
                result = await self.executor.execute(j)
                logger.info(f"[리밸런싱] {ticker} {side} {qty}주 → {result}")

            if actions:
                await self.ui.send_notification(
                    f"⚖️ 리밸런싱 완료: {', '.join(actions)}", "success"
                )
                await self._send_balance()
            else:
                await self.ui.send_notification(
                    "⚖️ 리밸런싱 불필요 — 이미 균형 잡힌 포지션", "info"
                )
            await self.ui.send_rebalance_result({"actions": actions})
        except Exception as e:
            logger.error(f"리밸런싱 오류: {e}", exc_info=True)
            await self.ui.send_notification(f"리밸런싱 실패: {e}", "error")

    # ── 장 전 준비 ───────────────────────────────────────────────────────────────

    async def _pre_market_prepare(self) -> None:
        """장 시작 전 데이터 프리패치 + 포지션 점검 + AI 자동 ON."""
        market_name = "국내" if self.market == "KR" else "해외"
        logger.info(f"{market_name} 장 전 준비 모드 시작")
        await self.ui.send_notification(f"📊 {market_name} 장 전 준비: 데이터 프리패치 중...", "info")
        try:
            if self.collector:
                await self.collector.prefetch()
            balance = await self.executor.api.get_balance()
            positions = balance.get("positions", [])
            pos_info = ", ".join(
                f"{p['ticker']} {p['qty']}주"
                for p in positions if p["qty"] > 0
            ) or "없음"
            risk = self.executor.risk.status

            # ── AI 자동 ON ─────────────────────────────────────────────────────
            mock_mode = get("toss.mock_mode", True)
            if not self.use_ai:
                self.use_ai = True
                logger.info(f"{market_name} 장 전 준비 → AI 자동 ON")
                await self.ui.send_notification(
                    f"🤖 {market_name} 장 전 준비 — AI 판단 자동 ON", "success"
                )
                await self.ui.send_config(
                    self.auto_trade, self.dry_run, mock_mode,
                    self.market, self.tickers, self.use_ai,
                    self.min_interval_minutes,
                )

            await self.ui.send_notification(
                f"✅ {market_name} 장 전 준비 완료 — 보유: {pos_info} | 일손실: {risk['daily_loss']:,.0f}원",
                "success",
            )
            await self._send_balance()
        except Exception as e:
            logger.error(f"장 전 준비 오류: {e}", exc_info=True)
            await self.ui.send_notification(f"장 전 준비 실패: {e}", "error")

    async def _pre_market_scheduler(self) -> None:
        """장 시작 10분 전 자동으로 pre-market 준비를 실행합니다."""
        while self._running:
            now = datetime.now()
            if self.market == "KR":
                target = now.replace(hour=8, minute=50, second=0, microsecond=0)
            else:
                target = now.replace(hour=22, minute=50, second=0, microsecond=0)
            if now.time() >= target.time():
                target = target + timedelta(days=1)
            wait_secs = (target - now).total_seconds()
            logger.debug(f"장 전 준비 대기: {wait_secs / 60:.1f}분")
            await asyncio.sleep(min(wait_secs, 3600))  # 최대 1시간 단위로 깨서 날짜 체크
            if self._running and abs((datetime.now() - target).total_seconds()) < 60:
                await self._pre_market_prepare()

    # ── 장 시간 감시 — AI 자동 ON/OFF ───────────────────────────────────────────

    async def _market_hours_watcher(self) -> None:
        """
        장 종료 시 AI 자동 OFF (토큰 절약).
        - KR: 15:30 이후 → AI OFF
        - US: 06:00 이후 → AI OFF
        장 전 준비(pre_market_prepare) 실행 시 AI 자동 ON.
        """
        # __init__에서 이미 장 시간 기준으로 초기화됨 — 별도 시작 체크 불필요
        was_open = self._is_market_open()

        while self._running:
            await asyncio.sleep(60)  # 1분마다 체크
            is_open = self._is_market_open()

            if was_open and not is_open:
                # 장 종료 감지 — AI + 자동매매 모두 OFF
                market_name = "국내" if self.market == "KR" else "해외"
                self.use_ai = False
                self.auto_trade = False
                logger.info(f"{market_name} 장 종료 감지 → AI·자동매매 자동 OFF")
                await self.ui.send_notification(
                    f"🔔 {market_name} 장이 마감되었습니다. 자동매매를 종료합니다.",
                    "warn",
                    duration=5000,
                )
                mock_mode = get("toss.mock_mode", True)
                await self.ui.send_config(
                    self.auto_trade, self.dry_run, mock_mode,
                    self.market, self.tickers, self.use_ai,
                    self.min_interval_minutes,
                )

            was_open = is_open

    # ── 컬렉터 워치독 ────────────────────────────────────────────────────────────

    async def _watchdog(self) -> None:
        """컬렉터 태스크가 예상치 못하게 종료되면 재시작합니다."""
        await asyncio.sleep(60)  # 초기 안정화 대기
        while self._running:
            await asyncio.sleep(30)
            if self._collector_task and self._collector_task.done():
                try:
                    exc = self._collector_task.exception()
                    logger.error(f"컬렉터 비정상 종료 ({exc}) — 재시작 중...")
                    await self.ui.send_notification("⚡ 데이터 수집기 재시작 중...", "warn")
                except asyncio.CancelledError:
                    pass  # 정상 취소된 경우 무시
                else:
                    await self._restart_collector()

    def _is_market_open(self) -> bool:
        """장 시간인지 확인합니다. US는 KST 23:30~06:00 (자정 걸침)."""
        now = datetime.now().time()
        if self.market == "US":
            open_t = datetime.strptime("23:30", "%H:%M").time()
            close_t = datetime.strptime("06:00", "%H:%M").time()
            return now >= open_t or now <= close_t
        open_t = datetime.strptime(get("trading.market_open", "09:00"), "%H:%M").time()
        close_t = datetime.strptime(get("trading.market_close", "15:30"), "%H:%M").time()
        return open_t <= now <= close_t

    # ── 실행 / 종료 ───────────────────────────────────────────────────────────

    async def run(self) -> None:
        """시스템을 시작합니다."""
        self._running = True
        mode = "DRY-RUN" if self.dry_run else "LIVE ⚠️"
        logger.info(f"═══ 자동매매 시스템 시작 [{mode}] ═══")
        logger.info(f"종목: {self.tickers}")

        # DB에서 손절가·목표가 복원
        saved = self.executor.load_position_limits()
        if saved:
            self._position_limits.update(saved)
            # 트레일링 스탑 고점을 목표가 기준으로 초기화 (재시작 시 보수적으로 시작)
            for t, lim in saved.items():
                if lim.get("target_price", 0) > 0:
                    self._trailing_highs[t] = lim["target_price"]
            logger.info(f"포지션 한도 복원: {list(saved.keys())}")

        # DB에서 AI 직전 신호 복원
        saved_signals = self.executor.load_last_signals()
        if saved_signals:
            self._last_signals.update(saved_signals)
            logger.info(f"AI 직전 신호 복원: {list(saved_signals.keys())}")

        self.collector = self._make_collector()
        self._collector_task = asyncio.create_task(self.collector.start())
        asyncio.create_task(self._watchdog())
        asyncio.create_task(self._pre_market_scheduler())
        asyncio.create_task(self._market_hours_watcher())

        try:
            await self.ui.start()
        except asyncio.CancelledError:
            pass
        finally:
            if self._collector_task and not self._collector_task.done():
                self._collector_task.cancel()
            await self.shutdown()

    async def shutdown(self) -> None:
        """종료 처리."""
        self._running = False
        logger.info("═══ 시스템 종료 ═══")
        logger.info(
            f"Gemini 총 호출: {self.judge.total_calls}회 | "
            f"남은 한도: {self.judge.rate_limit_status['remaining']}회"
        )
        risk_status = self.executor.risk.status
        logger.info(f"리스크 현황: {risk_status}")


# ── 진입점 ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="자동매매 시스템")
    parser.add_argument("--live", action="store_true", help="실전 모드 (기본: dry-run)")
    parser.add_argument("--config", default="config.yaml", help="설정 파일 경로")
    args = parser.parse_args()

    load_config(args.config)
    setup_logging()

    dry_run = not args.live
    if not dry_run:
        confirm = input("⚠️  실전 모드입니다. 계속하려면 'YES' 입력: ")
        if confirm.strip() != "YES":
            print("취소되었습니다.")
            sys.exit(0)

    system = TradingSystem(dry_run=dry_run)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
        loop.run_until_complete(system.run())
    except KeyboardInterrupt:
        logger.info("종료 신호 수신 (Ctrl+C)")
    finally:
        loop.close()


if __name__ == "__main__":
    main()