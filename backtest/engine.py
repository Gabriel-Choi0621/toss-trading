"""
백테스트 엔진
과거 OHLCV 데이터로 기술적 지표 기반 전략을 시뮬레이션합니다.
AI 판단 없이 룰 기반(RSI + MACD + 볼린저밴드)으로 매매 신호를 생성합니다.

사용 예시:
    from backtest.engine import BacktestEngine
    from collector.toss_api import generate_ohlcv

    df = generate_ohlcv("005930", count=500, base_price=75000)
    engine = BacktestEngine(initial_cash=10_000_000)
    result = engine.run(df, ticker="005930")
    print(result)
"""
import logging
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

from analyzer.technical import analyze

logger = logging.getLogger(__name__)


@dataclass
class Trade:
    """개별 거래 기록."""
    entry_date: str
    exit_date: str
    side: str   # "LONG"
    entry_price: float
    exit_price: float
    qty: int
    pnl: float
    pnl_pct: float
    exit_reason: str  # "signal" | "stop_loss" | "take_profit" | "end_of_data"


@dataclass
class BacktestResult:
    """백테스트 결과."""
    ticker: str
    initial_cash: float
    final_equity: float
    total_return_pct: float
    total_trades: int
    win_trades: int
    loss_trades: int
    win_rate: float
    avg_profit_pct: float
    avg_loss_pct: float
    profit_factor: float
    max_drawdown_pct: float
    sharpe_ratio: float
    trades: list = field(default_factory=list)
    equity_curve: list = field(default_factory=list)  # [{date, equity}, ...]

    def __str__(self) -> str:
        return (
            f"[{self.ticker}] 수익률={self.total_return_pct:+.2f}% "
            f"거래={self.total_trades}회 승률={self.win_rate:.1%} "
            f"MDD={self.max_drawdown_pct:.2f}% 샤프={self.sharpe_ratio:.2f}"
        )


class BacktestEngine:
    """
    룰 기반 백테스트 엔진.

    전략:
    - BUY 조건: RSI < 35 AND MACD 골든크로스 AND 볼린저밴드 하단 근처(bb_position < 0.3)
    - SELL 조건: RSI > 65 OR MACD 데드크로스 OR 볼린저밴드 상단 근처(bb_position > 0.7)
    - 손절: 진입가 대비 -5%
    - 익절: 진입가 대비 +10%
    """

    def __init__(
        self,
        initial_cash: float = 10_000_000,
        position_ratio: float = 0.95,    # 가용 현금의 95% 투입
        stop_loss_pct: float = 0.05,     # 5% 손절
        take_profit_pct: float = 0.10,   # 10% 익절
        commission_pct: float = 0.00195, # 매도 수수료+세금 (KR 기준)
        window: int = 30,               # 지표 계산 최소 캔들 수
    ):
        self.initial_cash = initial_cash
        self.position_ratio = position_ratio
        self.stop_loss_pct = stop_loss_pct
        self.take_profit_pct = take_profit_pct
        self.commission_pct = commission_pct
        self.window = window

    def run(self, df: pd.DataFrame, ticker: str = "UNKNOWN") -> BacktestResult:
        """
        백테스트를 실행합니다.

        Args:
            df: OHLCV DataFrame (최소 window * 2행 권장)
            ticker: 종목 코드 (결과 레이블용)

        Returns:
            BacktestResult
        """
        if df is None or len(df) < self.window + 5:
            logger.warning(f"데이터 부족 — 백테스트 불가 ({len(df) if df is not None else 0}행)")
            return self._empty_result(ticker)

        cash = self.initial_cash
        position: Optional[dict] = None  # {"qty", "entry_price", "entry_date", "stop", "target"}
        trades: list[Trade] = []
        equity_curve: list[dict] = []

        dates  = df.index.tolist()
        closes = df["close"].astype(float).tolist()

        for i in range(self.window, len(df)):
            window_df = df.iloc[:i + 1]
            indicators = analyze(window_df)
            if indicators is None:
                continue

            date  = str(dates[i])[:10]
            price = closes[i]
            equity = cash + (position["qty"] * price if position else 0)
            equity_curve.append({"date": date, "equity": round(equity, 2)})

            # ── 포지션 보유 중: 손절/익절/매도 신호 체크 ────────────────────
            if position:
                stop   = position["stop"]
                target = position["target"]
                exit_reason = None

                if price <= stop:
                    exit_reason = "stop_loss"
                elif price >= target:
                    exit_reason = "take_profit"
                elif self._sell_signal(indicators):
                    exit_reason = "signal"

                if exit_reason:
                    sell_price = price
                    commission = sell_price * position["qty"] * self.commission_pct
                    gross_pnl  = (sell_price - position["entry_price"]) * position["qty"]
                    net_pnl    = gross_pnl - commission
                    pnl_pct    = (sell_price - position["entry_price"]) / position["entry_price"] * 100

                    cash += sell_price * position["qty"] - commission
                    trades.append(Trade(
                        entry_date=position["entry_date"],
                        exit_date=date,
                        side="LONG",
                        entry_price=position["entry_price"],
                        exit_price=sell_price,
                        qty=position["qty"],
                        pnl=round(net_pnl, 2),
                        pnl_pct=round(pnl_pct, 2),
                        exit_reason=exit_reason,
                    ))
                    position = None

            # ── 포지션 없음: 매수 신호 체크 ─────────────────────────────────
            else:
                if self._buy_signal(indicators):
                    invest = cash * self.position_ratio
                    qty = int(invest / price)
                    if qty > 0:
                        cost = price * qty
                        cash -= cost
                        position = {
                            "qty":         qty,
                            "entry_price": price,
                            "entry_date":  date,
                            "stop":        round(price * (1 - self.stop_loss_pct), 2),
                            "target":      round(price * (1 + self.take_profit_pct), 2),
                        }

        # 데이터 끝에 포지션 남은 경우 강제 청산
        if position and closes:
            last_price = closes[-1]
            last_date  = str(dates[-1])[:10]
            commission = last_price * position["qty"] * self.commission_pct
            gross_pnl  = (last_price - position["entry_price"]) * position["qty"]
            net_pnl    = gross_pnl - commission
            pnl_pct    = (last_price - position["entry_price"]) / position["entry_price"] * 100
            cash += last_price * position["qty"] - commission
            trades.append(Trade(
                entry_date=position["entry_date"],
                exit_date=last_date,
                side="LONG",
                entry_price=position["entry_price"],
                exit_price=last_price,
                qty=position["qty"],
                pnl=round(net_pnl, 2),
                pnl_pct=round(pnl_pct, 2),
                exit_reason="end_of_data",
            ))

        return self._calc_result(ticker, cash, trades, equity_curve)

    # ── 전략 신호 ──────────────────────────────────────────────────────────────

    def _buy_signal(self, ind: dict) -> bool:
        """매수 신호: RSI 과매도 + MACD 골든크로스 + 볼린저밴드 하단."""
        rsi      = ind.get("rsi")
        macd_x   = ind.get("macd_cross", 0)
        bb_pos   = ind.get("bb_position")

        if rsi is None or bb_pos is None:
            return False
        return rsi < 35 and macd_x == 1 and bb_pos < 0.3

    def _sell_signal(self, ind: dict) -> bool:
        """매도 신호: RSI 과매수 OR MACD 데드크로스 OR 볼린저밴드 상단."""
        rsi    = ind.get("rsi")
        macd_x = ind.get("macd_cross", 0)
        bb_pos = ind.get("bb_position")

        if rsi is None:
            return False
        if rsi > 65:
            return True
        if macd_x == -1:
            return True
        if bb_pos is not None and bb_pos > 0.7:
            return True
        return False

    # ── 결과 계산 ──────────────────────────────────────────────────────────────

    def _calc_result(
        self,
        ticker: str,
        final_cash: float,
        trades: list[Trade],
        equity_curve: list[dict],
    ) -> BacktestResult:
        import statistics

        final_equity = final_cash
        total_return = (final_equity - self.initial_cash) / self.initial_cash * 100

        wins   = [t for t in trades if t.pnl > 0]
        losses = [t for t in trades if t.pnl <= 0]

        win_rate     = len(wins) / len(trades) if trades else 0.0
        avg_profit   = sum(t.pnl_pct for t in wins)   / len(wins)   if wins   else 0.0
        avg_loss     = sum(t.pnl_pct for t in losses) / len(losses) if losses else 0.0
        total_profit = sum(t.pnl for t in wins)
        total_loss   = abs(sum(t.pnl for t in losses))
        profit_factor = total_profit / total_loss if total_loss > 0 else 0.0

        # MDD
        mdd_pct = 0.0
        peak    = float("-inf")
        for point in equity_curve:
            eq = point["equity"]
            if eq > peak:
                peak = eq
            if peak > 0:
                dd = (peak - eq) / peak * 100
                if dd > mdd_pct:
                    mdd_pct = dd

        # 샤프 지수 (일별 수익률 기준)
        sharpe = 0.0
        if len(equity_curve) >= 2:
            daily_returns = []
            for j in range(1, len(equity_curve)):
                prev_eq = equity_curve[j - 1]["equity"]
                curr_eq = equity_curve[j]["equity"]
                if prev_eq > 0:
                    daily_returns.append((curr_eq - prev_eq) / prev_eq)
            if len(daily_returns) >= 2:
                mean_r = statistics.mean(daily_returns)
                std_r  = statistics.stdev(daily_returns)
                if std_r > 0:
                    sharpe = round(mean_r / std_r * (252 ** 0.5), 2)

        return BacktestResult(
            ticker=ticker,
            initial_cash=self.initial_cash,
            final_equity=round(final_equity, 2),
            total_return_pct=round(total_return, 2),
            total_trades=len(trades),
            win_trades=len(wins),
            loss_trades=len(losses),
            win_rate=round(win_rate, 3),
            avg_profit_pct=round(avg_profit, 2),
            avg_loss_pct=round(avg_loss, 2),
            profit_factor=round(profit_factor, 2),
            max_drawdown_pct=round(mdd_pct, 2),
            sharpe_ratio=sharpe,
            trades=trades,
            equity_curve=equity_curve,
        )

    def _empty_result(self, ticker: str) -> BacktestResult:
        return BacktestResult(
            ticker=ticker,
            initial_cash=self.initial_cash,
            final_equity=self.initial_cash,
            total_return_pct=0.0,
            total_trades=0,
            win_trades=0,
            loss_trades=0,
            win_rate=0.0,
            avg_profit_pct=0.0,
            avg_loss_pct=0.0,
            profit_factor=0.0,
            max_drawdown_pct=0.0,
            sharpe_ratio=0.0,
        )
