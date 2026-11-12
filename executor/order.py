"""
주문 실행기
리스크 체크 후 주문을 실행하고 SQLite에 기록합니다.
"""
import logging
import sqlite3
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Optional

from ai.judgment_engine import JudgmentResult
from collector.toss_api import TossAPIInterface
from executor.risk_manager import RiskManager
from config_loader import get

logger = logging.getLogger(__name__)


class OrderExecutor:
    """주문 실행 및 기록 담당."""

    def __init__(self, dry_run: Optional[bool] = None):
        self.dry_run = dry_run if dry_run is not None else get("trading.dry_run", True)
        self.api = TossAPIInterface(
            app_key=get("toss.app_key", ""),
            app_secret=get("toss.app_secret", ""),
            mock=get("toss.mock_mode", True),
        )
        self.risk = RiskManager()
        db_path = get("database.path", "data/trading.db")
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._db = db_path
        self._init_db()

        if self.dry_run:
            logger.warning("⚠️  DRY RUN 모드 — 실제 주문 실행 안 됩니다")

    # ── 퍼블릭 메서드 ─────────────────────────────────────────────────────────

    async def execute(self, judgment: JudgmentResult) -> dict:
        """
        판단 결과에 따라 주문을 실행합니다.

        Returns:
            주문 결과 딕셔너리
        """
        if judgment.signal == "HOLD":
            return {"status": "skipped", "reason": "HOLD 신호"}

        balance = await self.api.get_balance()
        total_asset = balance["total_asset"]
        cash = balance["cash"]
        positions = {p["ticker"]: p for p in balance.get("positions", [])}
        current_pos = positions.get(judgment.ticker)
        current_price = judgment.target_price  # 지정가 기준

        if judgment.signal == "BUY":
            # 매수 수량 계산
            qty = judgment.manual_qty if judgment.manual_qty > 0 else self._calc_qty(judgment.ticker, current_price, total_asset, cash)
            order_value = current_price * qty

            # 기존 보유 평가금액 (추가 매수 시 총 보유 한도 체크용)
            current_holding_value = int(current_pos["qty"] * current_pos["current_price"]) if current_pos else 0

            ok, reason = self.risk.check(judgment.ticker, order_value, total_asset, current_holding_value)
            if not ok:
                logger.warning(f"[{judgment.ticker}] 리스크 거부: {reason}")
                return {"status": "rejected", "reason": reason}

            return await self._buy(judgment, qty, current_price)

        else:  # SELL
            # 매도는 포지션 비중 체크 없이 쿨다운·일일 손실만 확인
            ok, reason = self.risk.check_sell(total_asset)
            if not ok:
                logger.warning(f"[{judgment.ticker}] 매도 리스크 거부: {reason}")
                return {"status": "rejected", "reason": reason}

            return await self._sell(judgment)

    async def execute_stop_loss(self, ticker: str, price: float) -> dict:
        """손절가 도달 시 즉시 시장가 매도. 쿨다운 무관하게 강제 실행."""
        j = JudgmentResult(
            ticker=ticker, signal="SELL", confidence=100,
            target_price=round(float(price), 2), stop_loss=0.0,
            target_return_pct=0.0, stop_loss_pct=0.0,
            reason="손절가 도달 — 자동 매도", risk_level="high",
        )
        balance = await self.api.get_balance()
        ok, reason = self.risk.check_sell(balance["total_asset"], force=True)
        if not ok:
            logger.warning(f"[{ticker}] 손절 매도 리스크 거부: {reason}")
            return {"status": "rejected", "reason": reason}
        return await self._sell(j)

    async def execute_take_profit(self, ticker: str, price: float, partial_qty: int = 0) -> dict:
        """
        목표가 도달 시 즉시 익절 매도.

        Args:
            ticker: 종목 코드
            price: 현재가
            partial_qty: 부분 매도 수량 (0이면 전량)
        """
        j = JudgmentResult(
            ticker=ticker, signal="SELL", confidence=100,
            target_price=round(float(price), 2), stop_loss=0.0,
            target_return_pct=0.0, stop_loss_pct=0.0,
            reason=f"목표가 도달 — 자동 익절{'(부분)' if partial_qty > 0 else ''}",
            risk_level="low",
            manual_qty=partial_qty,
        )
        balance = await self.api.get_balance()
        ok, reason = self.risk.check_sell(balance["total_asset"])
        if not ok:
            logger.warning(f"[{ticker}] 익절 매도 리스크 거부: {reason}")
            return {"status": "rejected", "reason": reason}
        return await self._sell(j)

    # ── 내부 메서드 ───────────────────────────────────────────────────────────

    def _calc_qty(self, ticker: str, price: float, total_asset: int, cash: int) -> int:
        """수수료를 포함한 실효 가격 기준으로 최대 매수 수량을 계산합니다."""
        buy_rate = get("trading.commission.kr_buy_rate", 0.00015)
        if ticker.isalpha() and ticker.isupper():
            buy_rate = get("trading.commission.us_buy_rate", 0.0025)
        effective_price = price * (1 + buy_rate)
        max_value = int(total_asset * self.risk.max_position_ratio)
        affordable = min(cash, max_value)
        qty = int(affordable / effective_price)
        return max(qty, 0)

    async def _buy(self, j: JudgmentResult, qty: int, price: float) -> dict:
        if qty <= 0:
            return {"status": "skipped", "reason": "수량 0 — 잔고 부족"}

        logger.info(
            f"[BUY] {j.ticker} {qty}주 @ {price:,}원 "
            f"(dry_run={self.dry_run})"
        )

        if self.dry_run:
            result = {
                "order_id": f"DRY-{j.ticker}-{int(datetime.now().timestamp())}",
                "status": "DRY_RUN",
                "ticker": j.ticker,
                "side": "BUY",
                "price": price,
                "qty": qty,
                "timestamp": datetime.now().isoformat(),
            }
        else:
            result = await self.api.place_order(j.ticker, "BUY", price, qty)

        buy_commission = self._commission(j.ticker, price, qty, "BUY")
        self._save_order(result, j, pnl=0.0, commission=buy_commission)
        return result

    async def _sell(self, j: JudgmentResult) -> dict:
        balance = await self.api.get_balance()
        positions = {p["ticker"]: p for p in balance.get("positions", [])}
        pos = positions.get(j.ticker)

        if not pos:
            return {"status": "skipped", "reason": f"{j.ticker} 보유 없음"}

        qty = j.manual_qty if j.manual_qty > 0 else pos["qty"]
        price = pos["current_price"]

        logger.info(
            f"[SELL] {j.ticker} {qty}주 @ {price:,}원 "
            f"(dry_run={self.dry_run})"
        )

        if self.dry_run:
            result = {
                "order_id": f"DRY-{j.ticker}-{int(datetime.now().timestamp())}",
                "status": "DRY_RUN",
                "ticker": j.ticker,
                "side": "SELL",
                "price": price,
                "qty": qty,
                "timestamp": datetime.now().isoformat(),
            }
        else:
            result = await self.api.place_order(j.ticker, "SELL", price, qty)

        # 수수료 차감 후 순손익 계산
        sell_commission = self._commission(j.ticker, price, qty, "SELL")
        buy_commission  = self._commission(j.ticker, pos["avg_price"], qty, "BUY")
        gross_pnl = (price - pos["avg_price"]) * qty
        net_pnl   = gross_pnl - sell_commission - buy_commission

        if net_pnl < 0:
            self.risk.record_loss(abs(net_pnl))
        else:
            self.risk.record_profit()

        result["pnl"] = round(net_pnl, 2)
        self._save_order(result, j, pnl=net_pnl, commission=sell_commission + buy_commission)
        return result

    # ── 수수료 계산 ───────────────────────────────────────────────────────────

    def _commission(self, ticker: str, price: float, qty: int, side: str) -> float:
        """수수료 + 세금 계산 (원화 금액 반환)."""
        is_us = ticker.isalpha() and ticker.isupper()
        cfg = get("trading.commission", {})
        if is_us:
            rate = cfg.get("us_buy_rate" if side == "BUY" else "us_sell_rate", 0.0025)
        else:
            rate = cfg.get("kr_buy_rate" if side == "BUY" else "kr_sell_rate", 0.00015 if side == "BUY" else 0.00195)
        return price * qty * rate

    # ── DB ───────────────────────────────────────────────────────────────────

    def _init_db(self) -> None:
        with sqlite3.connect(self._db) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    order_id TEXT,
                    ticker TEXT,
                    side TEXT,
                    price REAL,
                    qty INTEGER,
                    status TEXT,
                    signal TEXT,
                    confidence INTEGER,
                    reason TEXT,
                    pnl REAL DEFAULT 0,
                    commission REAL DEFAULT 0,
                    dry_run INTEGER,
                    timestamp TEXT
                )
            """)
            # 기존 테이블에 컬럼 추가 (마이그레이션)
            for col, typedef in [("pnl", "REAL DEFAULT 0"), ("commission", "REAL DEFAULT 0")]:
                try:
                    conn.execute(f"ALTER TABLE orders ADD COLUMN {col} {typedef}")
                except Exception:
                    pass  # 이미 존재

            # 손절가·목표가 영속 테이블
            conn.execute("""
                CREATE TABLE IF NOT EXISTS position_limits (
                    ticker TEXT PRIMARY KEY,
                    stop_loss REAL DEFAULT 0,
                    target_price REAL DEFAULT 0,
                    updated_at TEXT
                )
            """)

            # AI 직전 신호 영속 테이블 (재시작 후 연속성 보장)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS ai_signals (
                    ticker TEXT PRIMARY KEY,
                    signal TEXT,
                    confidence INTEGER,
                    reason TEXT,
                    updated_at TEXT
                )
            """)

    def _save_order(self, result: dict, j: JudgmentResult, pnl: float = 0.0, commission: float = 0.0) -> None:
        try:
            with sqlite3.connect(self._db) as conn:
                conn.execute(
                    """INSERT INTO orders
                       (order_id, ticker, side, price, qty, status,
                        signal, confidence, reason, pnl, commission, dry_run, timestamp)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        result.get("order_id", ""),
                        j.ticker,
                        result.get("side", j.signal),
                        result.get("price", 0),
                        result.get("qty", 0),
                        result.get("status", ""),
                        j.signal,
                        j.confidence,
                        j.reason,
                        round(pnl, 2),
                        round(commission, 2),
                        int(self.dry_run),
                        datetime.now().isoformat(),
                    ),
                )
        except Exception as e:
            logger.error(f"DB 저장 실패: {e}")

    def get_performance(self, days: int = 30) -> dict:
        """
        최근 N일간 매매 성과를 집계합니다.

        Returns:
            {
                "total_trades": int,
                "win_trades": int,
                "loss_trades": int,
                "win_rate": float,           # 0~1
                "total_pnl": float,
                "avg_profit": float,         # 승리 거래 평균 수익
                "avg_loss": float,           # 패배 거래 평균 손실
                "profit_factor": float,      # 총 수익 / 총 손실
                "mdd": float,               # 최대 낙폭 (절대값)
                "sharpe": float,            # 단순 샤프 지수 근사
                "daily_pnl": list[dict]     # [{date, pnl}, ...] 날짜별 손익
            }
        """
        from datetime import timedelta
        cutoff = (datetime.now() - timedelta(days=days)).date().isoformat()
        try:
            with sqlite3.connect(self._db) as conn:
                rows = conn.execute(
                    """SELECT DATE(timestamp), pnl
                       FROM orders
                       WHERE side = 'SELL'
                         AND DATE(timestamp) >= ?
                         AND dry_run = ?
                       ORDER BY timestamp""",
                    (cutoff, int(self.dry_run)),
                ).fetchall()
        except Exception as e:
            logger.error(f"성과 조회 실패: {e}")
            return {}

        if not rows:
            return {
                "total_trades": 0, "win_trades": 0, "loss_trades": 0,
                "win_rate": 0.0, "total_pnl": 0.0,
                "avg_profit": 0.0, "avg_loss": 0.0,
                "profit_factor": 0.0, "mdd": 0.0, "sharpe": 0.0,
                "daily_pnl": [],
            }

        pnl_values = [r[1] for r in rows]
        wins   = [p for p in pnl_values if p > 0]
        losses = [p for p in pnl_values if p <= 0]

        total_profit = sum(wins)
        total_loss   = abs(sum(losses))

        # 날짜별 손익 집계
        daily: dict[str, float] = {}
        for date_str, pnl in rows:
            daily[date_str] = daily.get(date_str, 0.0) + pnl

        # MDD 계산 (누적 손익 기준)
        cumulative = []
        total = 0.0
        for pnl in pnl_values:
            total += pnl
            cumulative.append(total)

        mdd = 0.0
        peak = float("-inf")
        for v in cumulative:
            if v > peak:
                peak = v
            drawdown = peak - v
            if drawdown > mdd:
                mdd = drawdown

        # 샤프 지수 근사 (일별 손익 기준, 무위험 수익률 0 가정)
        import statistics
        daily_returns = list(daily.values())
        sharpe = 0.0
        if len(daily_returns) >= 2:
            mean_ret  = statistics.mean(daily_returns)
            stdev_ret = statistics.stdev(daily_returns)
            if stdev_ret > 0:
                sharpe = round(mean_ret / stdev_ret * (252 ** 0.5), 2)  # 연환산

        return {
            "total_trades":  len(pnl_values),
            "win_trades":    len(wins),
            "loss_trades":   len(losses),
            "win_rate":      round(len(wins) / len(pnl_values), 3) if pnl_values else 0.0,
            "total_pnl":     round(sum(pnl_values), 2),
            "avg_profit":    round(total_profit / len(wins), 2) if wins else 0.0,
            "avg_loss":      round(-total_loss / len(losses), 2) if losses else 0.0,
            "profit_factor": round(total_profit / total_loss, 2) if total_loss > 0 else 0.0,
            "mdd":           round(mdd, 2),
            "sharpe":        sharpe,
            "daily_pnl":     [{"date": d, "pnl": round(p, 2)} for d, p in sorted(daily.items())],
        }

    def get_today_pnl(self) -> int:
        """오늘 체결된 매도 주문들의 순손익 합계를 반환합니다."""
        today = datetime.now().date().isoformat()
        try:
            with sqlite3.connect(self._db) as conn:
                row = conn.execute(
                    """SELECT COALESCE(SUM(pnl), 0) FROM orders
                       WHERE DATE(timestamp) = ? AND side = 'SELL' AND dry_run = ?""",
                    (today, int(self.dry_run)),
                ).fetchone()
            return int(row[0] or 0)
        except Exception as e:
            logger.error(f"today_pnl 조회 실패: {e}")
            return 0

    # ── 포지션 한도 DB ────────────────────────────────────────────────────────

    def save_position_limit(self, ticker: str, stop_loss: float, target_price: float) -> None:
        """손절가·목표가를 DB에 저장합니다."""
        try:
            with sqlite3.connect(self._db) as conn:
                conn.execute(
                    """INSERT OR REPLACE INTO position_limits
                       (ticker, stop_loss, target_price, updated_at) VALUES (?,?,?,?)""",
                    (ticker, stop_loss, target_price, datetime.now().isoformat()),
                )
        except Exception as e:
            logger.error(f"position_limit 저장 실패: {e}")

    def delete_position_limit(self, ticker: str) -> None:
        """손절가·목표가를 DB에서 삭제합니다."""
        try:
            with sqlite3.connect(self._db) as conn:
                conn.execute("DELETE FROM position_limits WHERE ticker = ?", (ticker,))
        except Exception as e:
            logger.error(f"position_limit 삭제 실패: {e}")

    def load_position_limits(self) -> dict:
        """저장된 모든 손절가·목표가를 로드합니다."""
        try:
            with sqlite3.connect(self._db) as conn:
                rows = conn.execute(
                    "SELECT ticker, stop_loss, target_price FROM position_limits"
                ).fetchall()
            return {r[0]: {"stop_loss": r[1], "target_price": r[2]} for r in rows}
        except Exception as e:
            logger.error(f"position_limits 로드 실패: {e}")
            return {}

    # ── AI 신호 영속화 ────────────────────────────────────────────────────────

    def save_last_signal(self, ticker: str, signal: str, confidence: int, reason: str = "") -> None:
        """AI 판단 결과를 DB에 저장합니다 (재시작 후 연속성 보장)."""
        try:
            with sqlite3.connect(self._db) as conn:
                conn.execute(
                    """INSERT OR REPLACE INTO ai_signals
                       (ticker, signal, confidence, reason, updated_at)
                       VALUES (?, ?, ?, ?, ?)""",
                    (ticker, signal, confidence, reason, datetime.now().isoformat()),
                )
        except Exception as e:
            logger.error(f"ai_signal 저장 실패: {e}")

    def get_orders_history(self, limit: int = 50) -> list:
        """최근 N개 주문 내역을 DB에서 반환합니다."""
        try:
            with sqlite3.connect(self._db) as conn:
                rows = conn.execute(
                    """SELECT order_id, ticker, side, price, qty, status,
                              signal, confidence, reason, pnl, commission, dry_run, timestamp
                       FROM orders ORDER BY id DESC LIMIT ?""",
                    (limit,),
                ).fetchall()
            return [
                {
                    "order_id": r[0], "ticker": r[1], "side": r[2],
                    "price": r[3], "qty": r[4], "status": r[5],
                    "signal": r[6], "confidence": r[7], "reason": r[8],
                    "pnl": r[9], "commission": r[10],
                    "dry_run": bool(r[11]), "timestamp": r[12],
                }
                for r in rows
            ]
        except Exception as e:
            logger.error(f"orders_history 조회 실패: {e}")
            return []

    def cancel_order(self, order_id: str) -> bool:
        """주문을 취소 상태로 마킹합니다."""
        try:
            with sqlite3.connect(self._db) as conn:
                conn.execute(
                    "UPDATE orders SET status = 'CANCELLED' WHERE order_id = ?",
                    (order_id,),
                )
            logger.info(f"주문 취소: {order_id}")
            return True
        except Exception as e:
            logger.error(f"주문 취소 실패: {e}")
            return False

    def load_last_signals(self) -> dict:
        """저장된 AI 신호를 로드합니다.

        Returns:
            {ticker: {"signal": str, "confidence": int, "reason": str}}
        """
        try:
            with sqlite3.connect(self._db) as conn:
                rows = conn.execute(
                    "SELECT ticker, signal, confidence, reason FROM ai_signals"
                ).fetchall()
            return {
                r[0]: {"signal": r[1], "confidence": r[2], "reason": r[3]}
                for r in rows
            }
        except Exception as e:
            logger.error(f"ai_signals 로드 실패: {e}")
            return {}
