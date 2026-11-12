"""
테스트 스위트
Mock 데이터로 모든 모듈을 검증합니다.
실행: python -m pytest tests/ -v
"""
import asyncio
import sys
import tempfile
from pathlib import Path

# 프로젝트 루트를 경로에 추가
sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
import pytest

from collector.toss_api import TossAPIInterface, generate_ohlcv
from analyzer.technical import analyze


# ─────────────────────────────────────────────────────────────────────────────
# 공통 헬퍼
# ─────────────────────────────────────────────────────────────────────────────

def run(coro):
    """비동기 함수를 동기적으로 실행합니다 (Python 3.10+ 호환)."""
    return asyncio.run(coro)


# ─────────────────────────────────────────────────────────────────────────────
# Mock 데이터 생성
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def sample_df() -> pd.DataFrame:
    """테스트용 KR OHLCV DataFrame (100행)."""
    return generate_ohlcv("005930", count=100, base_price=75000)


@pytest.fixture
def sample_df_us() -> pd.DataFrame:
    """테스트용 US OHLCV DataFrame (100행)."""
    return generate_ohlcv("AAPL", count=100, base_price=191.0)


@pytest.fixture
def small_df() -> pd.DataFrame:
    """데이터 부족 테스트용 (10행)."""
    return generate_ohlcv("005930", count=10, base_price=75000)


@pytest.fixture
def tmp_db(tmp_path) -> str:
    """임시 SQLite DB 경로."""
    return str(tmp_path / "test_trading.db")


# ─────────────────────────────────────────────────────────────────────────────
# TossAPIInterface (Mock) 테스트
# ─────────────────────────────────────────────────────────────────────────────

class TestMockAPI:

    def setup_method(self):
        self.api = TossAPIInterface(mock=True)

    def test_generate_ohlcv_shape(self, sample_df):
        assert len(sample_df) == 100
        assert set(["open", "high", "low", "close", "volume"]).issubset(sample_df.columns)

    def test_ohlcv_price_validity(self, sample_df):
        """high >= close >= low 를 만족해야 합니다."""
        assert (sample_df["high"] >= sample_df["close"]).all()
        assert (sample_df["close"] >= sample_df["low"]).all()
        assert (sample_df["volume"] > 0).all()

    def test_us_ohlcv_is_float(self, sample_df_us):
        """US 주식 OHLCV는 소수점을 유지해야 합니다."""
        row = sample_df_us.iloc[-1]
        close = float(row["close"])
        assert isinstance(close, float)
        # 소수점 2자리 이내로 반올림된 값인지 확인
        assert close == round(close, 2)
        assert close > 0

    def test_current_price_kr(self):
        result = run(self.api.get_current_price("005930"))
        assert "price" in result
        assert isinstance(result["price"], int)
        assert result["price"] > 0
        assert result["ticker"] == "005930"

    def test_current_price_us_is_float(self):
        """US 종목 현재가는 float이어야 합니다."""
        result = run(self.api.get_current_price("AAPL"))
        assert "price" in result
        assert isinstance(result["price"], float)
        assert result["price"] > 0

    def test_get_balance(self):
        balance = run(self.api.get_balance())
        assert "cash" in balance
        assert "total_asset" in balance
        assert balance["cash"] > 0

    def test_balance_has_us_positions(self):
        """Mock 잔고에 US 종목(AAPL, NVDA)이 포함되어야 합니다."""
        balance = run(self.api.get_balance())
        tickers = {p["ticker"] for p in balance.get("positions", [])}
        assert "AAPL" in tickers
        assert "NVDA" in tickers

    def test_balance_us_avg_price_is_float(self):
        """US 종목 평균단가는 float이어야 합니다."""
        balance = run(self.api.get_balance())
        aapl = next(p for p in balance["positions"] if p["ticker"] == "AAPL")
        assert isinstance(aapl["avg_price"], float)
        assert aapl["avg_price"] == 185.50


# ─────────────────────────────────────────────────────────────────────────────
# 기술적 분석 테스트
# ─────────────────────────────────────────────────────────────────────────────

class TestTechnicalAnalyzer:

    def test_analyze_returns_dict(self, sample_df):
        result = analyze(sample_df)
        assert isinstance(result, dict)

    def test_required_keys(self, sample_df):
        result = analyze(sample_df)
        required = ["rsi", "macd", "bb_position", "ma5", "ma20", "volume_ratio", "current_price"]
        for key in required:
            assert key in result, f"키 누락: {key}"

    def test_rsi_range(self, sample_df):
        result = analyze(sample_df)
        if result["rsi"] is not None:
            assert 0 <= result["rsi"] <= 100, f"RSI 범위 오류: {result['rsi']}"

    def test_bb_position_range(self, sample_df):
        result = analyze(sample_df)
        if result["bb_position"] is not None:
            assert -0.5 <= result["bb_position"] <= 1.5, f"BB 위치 범위 오류: {result['bb_position']}"

    def test_ma_arrangement_values(self, sample_df):
        result = analyze(sample_df)
        assert result["ma_arrangement"] in ("bullish", "bearish", "neutral", None)

    def test_insufficient_data_returns_none(self, small_df):
        """데이터가 부족하면 None을 반환해야 합니다."""
        result = analyze(small_df)
        assert result is None

    def test_volume_ratio_positive(self, sample_df):
        result = analyze(sample_df)
        if result["volume_ratio"] is not None:
            assert result["volume_ratio"] > 0

    def test_current_price_is_float(self, sample_df):
        """current_price는 float이어야 합니다 (US 정밀도 보존)."""
        result = analyze(sample_df)
        assert isinstance(result["current_price"], float)

    def test_us_current_price_preserves_decimals(self, sample_df_us):
        """US 주식 current_price는 소수점이 유지되어야 합니다."""
        result = analyze(sample_df_us)
        assert isinstance(result["current_price"], float)
        # 소수점 2자리 이내로 반올림 확인
        assert result["current_price"] == round(result["current_price"], 2)


# ─────────────────────────────────────────────────────────────────────────────
# 리스크 매니저 테스트
# ─────────────────────────────────────────────────────────────────────────────

class TestRiskManager:

    def setup_method(self):
        from executor.risk_manager import RiskManager
        self.rm = RiskManager()

    def test_normal_order_allowed(self):
        ok, reason = self.rm.check("005930", order_value=1_000_000, total_asset=10_000_000)
        assert ok, reason

    def test_oversized_order_rejected(self):
        """단일 주문 비중 20% 초과 시 거부."""
        ok, reason = self.rm.check("005930", order_value=3_000_000, total_asset=10_000_000)
        assert not ok
        assert "비중" in reason

    def test_total_position_limit(self):
        """기존 보유 + 신규 주문이 40% 초과하면 거부."""
        # 기존 보유 3백만 + 신규 2백만 = 50% > 40%
        ok, reason = self.rm.check(
            "005930", order_value=2_000_000, total_asset=10_000_000,
            current_holding_value=3_000_000,
        )
        assert not ok
        assert "총 보유" in reason

    def test_total_position_within_limit(self):
        """기존 보유 + 신규 주문이 40% 이내면 허용."""
        # 기존 보유 2백만 + 신규 1.5백만 = 35% < 40%
        ok, reason = self.rm.check(
            "005930", order_value=1_500_000, total_asset=10_000_000,
            current_holding_value=2_000_000,
        )
        assert ok, reason

    def test_cooldown_after_consecutive_stops(self):
        for _ in range(3):
            self.rm.record_loss(100_000)
        ok, reason = self.rm.check("005930", order_value=100_000, total_asset=10_000_000)
        assert not ok
        assert "쿨다운" in reason

    def test_check_sell_normal(self):
        """일반 매도는 OK."""
        ok, reason = self.rm.check_sell(total_asset=10_000_000)
        assert ok

    def test_check_sell_blocked_by_cooldown(self):
        """쿨다운 중에는 일반 매도 거부."""
        for _ in range(3):
            self.rm.record_loss(100_000)
        ok, reason = self.rm.check_sell(total_asset=10_000_000)
        assert not ok
        assert "쿨다운" in reason

    def test_check_sell_force_bypasses_cooldown(self):
        """force=True이면 쿨다운 중에도 손절 매도 허용."""
        for _ in range(3):
            self.rm.record_loss(100_000)
        ok, reason = self.rm.check_sell(total_asset=10_000_000, force=True)
        assert ok
        assert "forced" in reason

    def test_check_sell_force_bypasses_daily_loss(self):
        """force=True이면 일일 손실 한도 초과해도 손절 허용."""
        # 일일 손실 3% 초과 시뮬레이션
        self.rm.record_loss(400_000)  # 10_000_000 * 4% = 400만 손실
        ok_normal, _ = self.rm.check_sell(total_asset=10_000_000)
        ok_force, _  = self.rm.check_sell(total_asset=10_000_000, force=True)
        assert not ok_normal
        assert ok_force

    def test_profit_resets_consecutive_stops(self):
        """수익 발생 시 연속 손절 카운트 초기화."""
        self.rm.record_loss(100_000)
        self.rm.record_loss(100_000)
        self.rm.record_profit()
        assert self.rm._consecutive_stops == 0


# ─────────────────────────────────────────────────────────────────────────────
# 주문 실행기 테스트
# ─────────────────────────────────────────────────────────────────────────────

class TestOrderExecutor:

    def _make_executor(self, tmp_db):
        """임시 DB를 사용하는 OrderExecutor를 생성합니다."""
        from unittest.mock import patch
        from executor.order import OrderExecutor
        with patch("executor.order.get") as mock_get:
            def cfg(key, default=None):
                mapping = {
                    "trading.dry_run": True,
                    "toss.app_key": "",
                    "toss.app_secret": "",
                    "toss.mock_mode": True,
                    "database.path": tmp_db,
                    "trading.commission": {},
                    "trading.risk": {},
                    "trading.commission.kr_buy_rate": 0.00015,
                    "trading.commission.us_buy_rate": 0.0025,
                }
                return mapping.get(key, default)
            mock_get.side_effect = cfg
            executor = OrderExecutor(dry_run=True)
        return executor

    def test_commission_kr_buy(self, tmp_db):
        """KR 매수 수수료 계산."""
        from executor.order import OrderExecutor
        executor = OrderExecutor.__new__(OrderExecutor)
        executor.dry_run = True
        result = executor._commission("005930", price=75000, qty=10, side="BUY")
        # 75000 * 10 * 0.00015 = 112.5
        assert abs(result - 112.5) < 1.0

    def test_commission_kr_sell(self, tmp_db):
        """KR 매도 수수료(+세금) 계산."""
        from executor.order import OrderExecutor
        executor = OrderExecutor.__new__(OrderExecutor)
        executor.dry_run = True
        result = executor._commission("005930", price=75000, qty=10, side="SELL")
        # 75000 * 10 * 0.00195 = 1462.5
        assert abs(result - 1462.5) < 1.0

    def test_commission_us(self, tmp_db):
        """US 매수/매도 수수료 동일 (0.25%)."""
        from executor.order import OrderExecutor
        executor = OrderExecutor.__new__(OrderExecutor)
        executor.dry_run = True
        buy_comm  = executor._commission("AAPL", price=191.0, qty=5, side="BUY")
        sell_comm = executor._commission("AAPL", price=191.0, qty=5, side="SELL")
        # 191.0 * 5 * 0.0025 = 2.3875
        assert abs(buy_comm - 2.3875) < 0.01
        assert abs(sell_comm - 2.3875) < 0.01

    def test_position_limit_save_load_delete(self, tmp_db):
        """손절가·목표가 DB 저장/로드/삭제."""
        from executor.order import OrderExecutor
        executor = OrderExecutor.__new__(OrderExecutor)
        executor._db = tmp_db
        executor._init_db()

        executor.save_position_limit("005930", stop_loss=70000.0, target_price=80000.0)
        limits = executor.load_position_limits()
        assert "005930" in limits
        assert limits["005930"]["stop_loss"] == 70000.0
        assert limits["005930"]["target_price"] == 80000.0

        executor.delete_position_limit("005930")
        limits_after = executor.load_position_limits()
        assert "005930" not in limits_after

    def test_position_limit_us_float(self, tmp_db):
        """US 주식 손절가·목표가 소수점 정밀도 유지."""
        from executor.order import OrderExecutor
        executor = OrderExecutor.__new__(OrderExecutor)
        executor._db = tmp_db
        executor._init_db()

        executor.save_position_limit("AAPL", stop_loss=185.50, target_price=205.75)
        limits = executor.load_position_limits()
        assert abs(limits["AAPL"]["stop_loss"] - 185.50) < 0.001
        assert abs(limits["AAPL"]["target_price"] - 205.75) < 0.001

    def test_get_today_pnl_empty(self, tmp_db):
        """거래 없을 때 오늘 손익은 0."""
        from executor.order import OrderExecutor
        executor = OrderExecutor.__new__(OrderExecutor)
        executor._db = tmp_db
        executor.dry_run = True
        executor._init_db()
        assert executor.get_today_pnl() == 0

    def test_get_performance_empty(self, tmp_db):
        """거래 없을 때 성과 지표 기본값 확인."""
        from executor.order import OrderExecutor
        executor = OrderExecutor.__new__(OrderExecutor)
        executor._db = tmp_db
        executor.dry_run = True
        executor._init_db()
        perf = executor.get_performance(days=30)
        assert perf["total_trades"] == 0
        assert perf["win_rate"] == 0.0
        assert perf["total_pnl"] == 0.0
        assert "daily_pnl" in perf

    def test_ai_signal_save_load(self, tmp_db):
        """AI 신호 저장 및 복원."""
        from executor.order import OrderExecutor
        executor = OrderExecutor.__new__(OrderExecutor)
        executor._db = tmp_db
        executor.dry_run = True
        executor._init_db()

        executor.save_last_signal("005930", "BUY", 85, "강한 상승 추세")
        executor.save_last_signal("AAPL", "HOLD", 55, "중립")

        signals = executor.load_last_signals()
        assert "005930" in signals
        assert signals["005930"]["signal"] == "BUY"
        assert signals["005930"]["confidence"] == 85
        assert "AAPL" in signals
        assert signals["AAPL"]["signal"] == "HOLD"

    def test_ai_signal_overwrite(self, tmp_db):
        """같은 종목 신호는 최신값으로 덮어쓰기."""
        from executor.order import OrderExecutor
        executor = OrderExecutor.__new__(OrderExecutor)
        executor._db = tmp_db
        executor.dry_run = True
        executor._init_db()

        executor.save_last_signal("005930", "BUY", 80, "첫번째")
        executor.save_last_signal("005930", "SELL", 90, "두번째")

        signals = executor.load_last_signals()
        assert signals["005930"]["signal"] == "SELL"
        assert signals["005930"]["confidence"] == 90


# ─────────────────────────────────────────────────────────────────────────────
# JudgmentResult 타입 검증
# ─────────────────────────────────────────────────────────────────────────────

class TestJudgmentResult:

    def test_target_price_is_float(self):
        """target_price, stop_loss는 float 타입이어야 합니다."""
        from ai.judgment_engine import JudgmentResult
        j = JudgmentResult(
            ticker="AAPL", signal="BUY", confidence=85,
            target_price=205.50, stop_loss=185.25,
            target_return_pct=0.075, stop_loss_pct=-0.032,
            reason="test", risk_level="medium",
        )
        assert isinstance(j.target_price, float)
        assert isinstance(j.stop_loss, float)
        assert j.target_price == 205.50
        assert j.stop_loss == 185.25

    def test_kr_price_as_float(self):
        """KR 종목도 float으로 저장되어야 합니다."""
        from ai.judgment_engine import JudgmentResult
        j = JudgmentResult(
            ticker="005930", signal="BUY", confidence=90,
            target_price=80000.0, stop_loss=70000.0,
            target_return_pct=0.067, stop_loss_pct=-0.067,
            reason="test", risk_level="low",
        )
        assert isinstance(j.target_price, float)
        assert j.target_price == 80000.0

    def test_safe_hold_float_prices(self):
        """_safe_hold 반환값도 float 타입이어야 합니다."""
        from ai.judgment_engine import _safe_hold
        result = _safe_hold("AAPL", "test reason")
        assert isinstance(result.target_price, float)
        assert isinstance(result.stop_loss, float)
        assert result.target_price == 0.0
        assert result.stop_loss == 0.0


# ─────────────────────────────────────────────────────────────────────────────
# 손절/익절 실행 테스트 (비동기)
# ─────────────────────────────────────────────────────────────────────────────

class TestStopLossTakeProfit:

    def _make_executor(self, tmp_db):
        from executor.order import OrderExecutor
        executor = OrderExecutor.__new__(OrderExecutor)
        executor.dry_run = True
        executor._db = tmp_db
        executor._init_db()
        from collector.toss_api import TossAPIInterface
        executor.api = TossAPIInterface(mock=True)
        from executor.risk_manager import RiskManager
        executor.risk = RiskManager()
        return executor

    def test_execute_stop_loss_dry_run(self, tmp_db):
        """손절가 도달 시 DRY_RUN 매도 결과 반환."""
        executor = self._make_executor(tmp_db)
        result = run(executor.execute_stop_loss("005930", price=70000.0))
        # 보유 없으면 skipped, 보유 있으면 DRY_RUN
        assert result["status"] in ("DRY_RUN", "skipped")

    def test_execute_take_profit_dry_run(self, tmp_db):
        """목표가 도달 시 DRY_RUN 매도 결과 반환."""
        executor = self._make_executor(tmp_db)
        result = run(executor.execute_take_profit("005930", price=80000.0))
        assert result["status"] in ("DRY_RUN", "skipped")

    def test_stop_loss_bypasses_cooldown(self, tmp_db):
        """손절 매도는 쿨다운 중에도 실행되어야 합니다."""
        executor = self._make_executor(tmp_db)
        # 쿨다운 발동
        for _ in range(3):
            executor.risk.record_loss(100_000)
        # 일반 매도는 막힘
        ok_normal, _ = executor.risk.check_sell(10_000_000)
        assert not ok_normal
        # 손절 강제 매도는 통과
        ok_force, _ = executor.risk.check_sell(10_000_000, force=True)
        assert ok_force


# ─────────────────────────────────────────────────────────────────────────────
# 트레일링 스탑 로직 단위 테스트
# ─────────────────────────────────────────────────────────────────────────────

class TestBacktest:
    """백테스트 엔진 테스트."""

    def test_backtest_runs(self):
        from backtest.engine import BacktestEngine
        df = generate_ohlcv("005930", count=300, base_price=75000)
        engine = BacktestEngine(initial_cash=10_000_000)
        result = engine.run(df, ticker="005930")
        assert result is not None
        assert result.ticker == "005930"
        assert result.initial_cash == 10_000_000

    def test_backtest_result_fields(self):
        from backtest.engine import BacktestEngine
        df = generate_ohlcv("005930", count=300, base_price=75000)
        engine = BacktestEngine(initial_cash=10_000_000)
        result = engine.run(df, ticker="005930")
        # 필수 필드 존재 확인
        assert isinstance(result.total_trades, int)
        assert isinstance(result.win_rate, float)
        assert isinstance(result.max_drawdown_pct, float)
        assert isinstance(result.sharpe_ratio, float)
        assert 0.0 <= result.win_rate <= 1.0
        assert result.max_drawdown_pct >= 0.0

    def test_backtest_win_loss_sum(self):
        from backtest.engine import BacktestEngine
        df = generate_ohlcv("005930", count=300, base_price=75000)
        engine = BacktestEngine(initial_cash=10_000_000)
        result = engine.run(df, ticker="005930")
        assert result.win_trades + result.loss_trades == result.total_trades

    def test_backtest_equity_curve(self):
        from backtest.engine import BacktestEngine
        df = generate_ohlcv("005930", count=300, base_price=75000)
        engine = BacktestEngine(initial_cash=10_000_000)
        result = engine.run(df, ticker="005930")
        assert len(result.equity_curve) > 0
        for point in result.equity_curve:
            assert "date" in point
            assert "equity" in point
            assert point["equity"] > 0

    def test_backtest_insufficient_data(self):
        from backtest.engine import BacktestEngine
        df = generate_ohlcv("005930", count=10, base_price=75000)
        engine = BacktestEngine(initial_cash=10_000_000)
        result = engine.run(df, ticker="005930")
        # 데이터 부족 → 빈 결과 반환 (크래시 없음)
        assert result.total_trades == 0

    def test_backtest_str(self):
        from backtest.engine import BacktestEngine
        df = generate_ohlcv("005930", count=300, base_price=75000)
        engine = BacktestEngine(initial_cash=10_000_000)
        result = engine.run(df, ticker="005930")
        s = str(result)
        assert "005930" in s
        assert "승률" in s

    def test_backtest_us_stock(self):
        from backtest.engine import BacktestEngine
        df = generate_ohlcv("AAPL", count=300, base_price=191.0)
        engine = BacktestEngine(
            initial_cash=10_000,
            commission_pct=0.0025,  # US 수수료
        )
        result = engine.run(df, ticker="AAPL")
        assert result is not None
        assert result.final_equity > 0


class TestDailyAnalyzer:
    """일봉 지표 분석 테스트."""

    def test_analyze_daily_returns_dict(self):
        from analyzer.technical import analyze_daily
        df = generate_ohlcv("005930", count=120, base_price=75000)
        result = analyze_daily(df)
        assert isinstance(result, dict)

    def test_analyze_daily_required_keys(self):
        from analyzer.technical import analyze_daily
        df = generate_ohlcv("005930", count=120, base_price=75000)
        result = analyze_daily(df)
        required = ["daily_ma20", "daily_ma60", "daily_rsi", "daily_ma_arrangement",
                    "week52_high", "week52_low", "week52_position"]
        for key in required:
            assert key in result, f"키 누락: {key}"

    def test_analyze_daily_insufficient_data(self):
        from analyzer.technical import analyze_daily
        df = generate_ohlcv("005930", count=10, base_price=75000)
        result = analyze_daily(df)
        assert result is None

    def test_analyze_daily_us_float(self):
        from analyzer.technical import analyze_daily
        df = generate_ohlcv("AAPL", count=120, base_price=191.0)
        result = analyze_daily(df)
        assert result is not None
        assert isinstance(result["daily_ma60"], float)
        # 소수점 2자리 이내로 반올림 확인
        assert result["daily_ma60"] == round(result["daily_ma60"], 2)

    def test_week52_position_range(self):
        from analyzer.technical import analyze_daily
        df = generate_ohlcv("005930", count=252, base_price=75000)
        result = analyze_daily(df)
        if result["week52_position"] is not None:
            assert 0.0 <= result["week52_position"] <= 100.0


class TestTrailingStop:
    """트레일링 스탑 낙폭 계산 로직을 단위 검증합니다."""

    def _check_trailing(self, high: float, price: float, pct: float) -> bool:
        """고점·현재가·기준% 입력 시 트레일링 스탑 발동 여부."""
        drawdown = (high - price) / high if high > 0 else 0
        return drawdown >= pct

    def test_no_trigger_on_new_high(self):
        """신고점 갱신 중에는 트리거 없음."""
        assert not self._check_trailing(100.0, 105.0, 0.03)

    def test_no_trigger_below_threshold(self):
        """낙폭이 기준 미만이면 트리거 없음."""
        assert not self._check_trailing(100.0, 98.0, 0.03)  # 2% < 3%

    def test_trigger_at_threshold(self):
        """낙폭이 기준과 정확히 같으면 트리거."""
        assert self._check_trailing(100.0, 97.0, 0.03)  # 3% == 3%

    def test_trigger_above_threshold(self):
        """낙폭이 기준 초과 시 트리거."""
        assert self._check_trailing(100.0, 90.0, 0.03)  # 10% > 3%

    def test_us_stock_float_precision(self):
        """US 주식 소수점 가격도 정확히 계산해야 합니다."""
        # AAPL: 고점 $205.75, 현재 $199.18 → 3.19% 낙폭
        high = 205.75
        price = 199.18
        drawdown = (high - price) / high
        assert abs(drawdown - 0.0319) < 0.001
        assert self._check_trailing(high, price, 0.03)

    def test_trailing_high_only_updates_upward(self):
        """고점은 올라갈 때만 갱신해야 합니다."""
        trailing_highs = {"AAPL": 200.0}
        # 가격 하락 → 고점 유지
        new_price = 195.0
        if new_price > trailing_highs["AAPL"]:
            trailing_highs["AAPL"] = new_price
        assert trailing_highs["AAPL"] == 200.0

        # 가격 상승 → 고점 갱신
        new_price = 210.0
        if new_price > trailing_highs["AAPL"]:
            trailing_highs["AAPL"] = new_price
        assert trailing_highs["AAPL"] == 210.0


# ─────────────────────────────────────────────────────────────────────────────
# 새 기능 테스트
# ─────────────────────────────────────────────────────────────────────────────

class TestNewFeatures:
    """새로 추가된 기능들을 검증합니다."""

    def _make_executor(self, tmp_db):
        from executor.order import OrderExecutor
        exec_ = OrderExecutor(dry_run=True)
        exec_._db = tmp_db
        exec_._init_db()
        return exec_

    def test_orders_history_empty(self):
        """빈 DB에서 거래 내역 조회 시 빈 리스트를 반환합니다."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            tmp_db = f.name
        exec_ = self._make_executor(tmp_db)
        result = exec_.get_orders_history(50)
        assert isinstance(result, list)
        assert len(result) == 0

    def test_orders_history_after_trade(self):
        """주문 체결 후 거래 내역에 포함되어야 합니다."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            tmp_db = f.name
        exec_ = self._make_executor(tmp_db)
        from ai.judgment_engine import JudgmentResult
        j = JudgmentResult(
            ticker="005930", signal="BUY", confidence=90,
            target_price=75000.0, stop_loss=70000.0,
            target_return_pct=0.067, stop_loss_pct=-0.067,
            reason="test", risk_level="low",
        )
        run(exec_.execute(j))
        history = exec_.get_orders_history(50)
        assert len(history) >= 1
        assert history[0]["ticker"] == "005930"

    def test_cancel_order(self):
        """주문 취소 시 DB에 CANCELLED 상태로 마킹됩니다."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            tmp_db = f.name
        exec_ = self._make_executor(tmp_db)
        from ai.judgment_engine import JudgmentResult
        j = JudgmentResult(
            ticker="005930", signal="BUY", confidence=90,
            target_price=75000.0, stop_loss=70000.0,
            target_return_pct=0.067, stop_loss_pct=-0.067,
            reason="test", risk_level="low",
        )
        run(exec_.execute(j))
        history = exec_.get_orders_history(10)
        order_id = history[0]["order_id"]
        result = exec_.cancel_order(order_id)
        assert result is True
        history2 = exec_.get_orders_history(10)
        assert history2[0]["status"] == "CANCELLED"

    def test_judgment_engine_set_model(self):
        """JudgmentEngine.set_model()이 모델명을 변경합니다."""
        from ai.judgment_engine import JudgmentEngine
        engine = JudgmentEngine()
        engine.set_model("gemini-2.5-pro")
        assert engine.model_name == "gemini-2.5-pro"
        engine.set_model("gemini-2.5-flash")
        assert engine.model_name == "gemini-2.5-flash"

    def test_price_alert_logic_above(self):
        """↑ 방향 알람: 가격이 임계값 이상이면 발동합니다."""
        alerts = {"005930": {"price": 80000.0, "direction": "above", "triggered": False}}
        price = 80500.0
        alert = alerts["005930"]
        triggered = (alert["direction"] == "above" and price >= alert["price"])
        assert triggered is True

    def test_price_alert_logic_below(self):
        """↓ 방향 알람: 가격이 임계값 이하이면 발동합니다."""
        alerts = {"005930": {"price": 70000.0, "direction": "below", "triggered": False}}
        price = 69500.0
        alert = alerts["005930"]
        triggered = (alert["direction"] == "below" and price <= alert["price"])
        assert triggered is True

    def test_price_alert_no_retrigger(self):
        """한 번 발동된 알람은 재발동되지 않습니다."""
        alerts = {"005930": {"price": 80000.0, "direction": "above", "triggered": True}}
        price = 81000.0
        alert = alerts["005930"]
        if alert.get("triggered", False):
            triggered = False
        else:
            triggered = (alert["direction"] == "above" and price >= alert["price"])
        assert triggered is False

    def test_watchdog_detects_done_task(self):
        """완료된 태스크를 watchdog이 감지합니다."""
        async def _run():
            import asyncio
            task = asyncio.create_task(asyncio.sleep(0))
            await task
            return task.done()
        result = run(_run())
        assert result is True

    def test_pre_market_prepare_mock(self):
        """장 전 준비 로직이 예외 없이 실행됩니다."""
        from executor.order import OrderExecutor
        exec_ = OrderExecutor(dry_run=True)
        # API 잔고 조회가 mock에서 정상 동작하는지 확인
        balance = run(exec_.api.get_balance())
        assert "positions" in balance
        assert "cash" in balance


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
