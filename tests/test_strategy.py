"""
tests/test_strategy.py
======================
Unit tests for strategy logic, order state transitions, risk sizing,
and indicator calculations.

Run with::

    pytest tests/ -v
"""

import sys
from pathlib import Path

# Ensure project root is importable
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

import pytest
import pandas as pd
import numpy as np

# ---------------------------------------------------------------------------
# Indicator tests
# ---------------------------------------------------------------------------

from app.utils.indicators import sma, rsi, momentum


class TestSMA:
    """Test Simple Moving Average calculation."""

    def test_sma_basic(self):
        s = pd.Series([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], dtype=float)
        result = sma(s, 3)
        # SMA(3) at index 2 = (1+2+3)/3 = 2.0
        assert result.iloc[2] == pytest.approx(2.0)
        # SMA(3) at index 9 = (8+9+10)/3 = 9.0
        assert result.iloc[9] == pytest.approx(9.0)

    def test_sma_leading_nan(self):
        s = pd.Series([10, 20, 30, 40, 50], dtype=float)
        result = sma(s, 3)
        assert pd.isna(result.iloc[0])
        assert pd.isna(result.iloc[1])
        assert not pd.isna(result.iloc[2])

    def test_sma_single_element_period(self):
        s = pd.Series([5, 10, 15], dtype=float)
        result = sma(s, 1)
        assert result.iloc[0] == pytest.approx(5.0)
        assert result.iloc[2] == pytest.approx(15.0)


class TestRSI:
    """Test RSI calculation."""

    def test_rsi_range(self):
        """RSI must be between 0 and 100."""
        np.random.seed(42)
        prices = pd.Series(np.cumsum(np.random.randn(100)) + 100)
        result = rsi(prices, 14)
        valid = result.dropna()
        assert (valid >= 0).all()
        assert (valid <= 100).all()

    def test_rsi_uptrend(self):
        """Pure uptrend should have RSI near 100."""
        s = pd.Series([float(i) for i in range(1, 50)])
        result = rsi(s, 14)
        # Last RSI should be very high
        assert result.iloc[-1] > 90


class TestMomentum:
    """Test momentum indicator."""

    def test_momentum_positive(self):
        s = pd.Series([10, 12, 14, 16, 18, 20], dtype=float)
        result = momentum(s, 3)
        # momentum(3) at index 3 = 16 - 10 = 6
        assert result.iloc[3] == pytest.approx(6.0)


# ---------------------------------------------------------------------------
# Risk sizing tests
# ---------------------------------------------------------------------------

from app.utils.risk import position_size, stop_loss_price, take_profit_price, RiskParams


class TestRiskSizing:
    """Test position sizing and stop/take-profit calculations."""

    def test_position_size_default(self):
        """1 lakh capital, 1% risk, 2% SL @ price 100 → risk=1000, risk_per_share=2 → qty=500.
        Now capped by MAX_POSITION_SIZE_PER_TRADE (default 500)."""
        params = RiskParams(capital=100_000, risk_pct=1.0, stop_loss_pct=2.0)
        qty = position_size(100.0, params)
        assert qty == 500

    def test_position_size_min_one(self):
        """Even with very low capital, qty should be at least 1."""
        params = RiskParams(capital=10, risk_pct=1.0, stop_loss_pct=2.0)
        qty = position_size(10000.0, params)
        assert qty >= 1

    def test_stop_loss_buy(self):
        sl = stop_loss_price(100.0, "BUY", 2.0)
        assert sl == pytest.approx(98.0)

    def test_stop_loss_sell(self):
        sl = stop_loss_price(100.0, "SELL", 2.0)
        assert sl == pytest.approx(102.0)

    def test_take_profit_buy(self):
        tp = take_profit_price(100.0, "BUY", 4.0)
        assert tp == pytest.approx(104.0)

    def test_take_profit_sell(self):
        tp = take_profit_price(100.0, "SELL", 4.0)
        assert tp == pytest.approx(96.0)


# ---------------------------------------------------------------------------
# CapitalManager tests
# ---------------------------------------------------------------------------

from app.broker.capital_manager import CapitalManager


class TestCapitalManager:
    """Test centralised capital and position management."""

    def test_initial_state(self):
        cm = CapitalManager(initial_capital=100_000)
        assert cm.initial_capital == 100_000
        assert cm.available_capital == 100_000
        assert cm.used_margin == 0.0
        assert cm.open_position_count == 0

    def test_update_position_buy(self):
        cm = CapitalManager(initial_capital=100_000)
        pnl = cm.update_position("TEST.NS", "BUY", 10, 2000.0)
        assert pnl == 0.0  # opening position has no PnL
        pos = cm.get_position("TEST.NS")
        assert pos["qty"] == 10
        assert pos["side"] == "BUY"
        assert pos["avg_price"] == 2000.0

    def test_roundtrip_pnl(self):
        cm = CapitalManager(initial_capital=100_000)
        cm.update_position("TEST.NS", "BUY", 10, 2000.0)
        pnl = cm.update_position("TEST.NS", "SELL", 10, 2100.0)
        assert pnl == pytest.approx(1000.0)  # (2100-2000)*10
        assert cm.realised_pnl == pytest.approx(1000.0)

    def test_clamp_quantity(self):
        cm = CapitalManager(
            initial_capital=10_000,
            max_position_size=100,
            max_qty_per_order=50,
        )
        # Should be capped by max_qty_per_order (50)
        assert cm.clamp_quantity(200, 100.0) == 50

    def test_daily_loss_halt(self):
        cm = CapitalManager(initial_capital=100_000, daily_loss_limit=1000)
        assert not cm.daily_loss_halted
        # Simulate a big loss
        cm.update_position("X.NS", "BUY", 100, 1000.0)
        cm.update_position("X.NS", "SELL", 100, 989.0)  # loss = 1100
        assert cm.check_daily_loss() is True
        assert cm.daily_loss_halted is True


# ---------------------------------------------------------------------------
# OrderValidator tests
# ---------------------------------------------------------------------------

from app.broker.order_validator import OrderValidator


class TestOrderValidator:
    """Test pre-trade validation."""

    def _make_validator(self, **cm_kwargs):
        cm = CapitalManager(initial_capital=100_000, **cm_kwargs)
        ov = OrderValidator(capital_manager=cm, cooldown_candles=3)
        return ov, cm

    def test_approved_signal(self):
        ov, _ = self._make_validator()
        result = ov.validate_signal(
            {"symbol": "TEST.NS", "action": "BUY", "price": 100.0}
        )
        assert result is None  # approved

    def test_cooldown_blocks_duplicate(self):
        ov, _ = self._make_validator()
        sig = {"symbol": "TEST.NS", "action": "BUY", "price": 100.0}
        ov.validate_signal(sig)
        ov.record_signal("TEST.NS")
        ov.tick()
        result = ov.validate_signal(
            {"symbol": "TEST.NS", "action": "BUY", "price": 101.0}
        )
        assert result is not None  # blocked by cooldown

    def test_daily_loss_blocks(self):
        ov, cm = self._make_validator(daily_loss_limit=100)
        cm.update_position("X.NS", "BUY", 100, 1000.0)
        cm.update_position("X.NS", "SELL", 100, 998.0)  # loss=200
        result = ov.validate_signal({"symbol": "Y.NS", "action": "BUY", "price": 50.0})
        assert result is not None
        assert "daily_loss" in result


# ---------------------------------------------------------------------------
# Order state transition tests
# ---------------------------------------------------------------------------

from app.broker.order_manager import OrderManager


class TestOrderManager:
    """Test order lifecycle and state transitions."""

    def _make_manager(self):
        """Create a fresh order manager with CapitalManager and no-op broker."""
        cm = CapitalManager(initial_capital=1_000_000)
        return OrderManager(
            broker_submit_fn=lambda order: True,
            capital_mgr=cm,
        )

    def test_place_manual_order(self):
        mgr = self._make_manager()
        order = mgr.place_manual_order("RELIANCE.NS", "BUY", 10, 2500.0)
        assert order["status"] == "NEW"
        assert order["symbol"] == "RELIANCE.NS"
        assert order["qty"] == 10

    def test_valid_state_transition(self):
        mgr = self._make_manager()
        order = mgr.place_manual_order("TCS.NS", "BUY", 5, 3500.0)
        oid = order["order_id"]

        # NEW → ACK
        updated = mgr.update_order_status(oid, "ACK")
        assert updated is not None
        assert updated["status"] == "ACK"

        # ACK → FILLED
        updated = mgr.update_order_status(oid, "FILLED", filled_qty=5, avg_price=3500.0)
        assert updated["status"] == "FILLED"

    def test_invalid_state_transition(self):
        mgr = self._make_manager()
        order = mgr.place_manual_order("INFY.NS", "SELL", 3, 1500.0)
        oid = order["order_id"]

        # NEW → FILLED directly should fail (must go through ACK)
        updated = mgr.update_order_status(oid, "FILLED")
        assert updated is None  # rejected

    def test_cancel_open_order(self):
        mgr = self._make_manager()
        order = mgr.place_manual_order("SBIN.NS", "BUY", 20, 600.0)
        oid = order["order_id"]

        ok = mgr.cancel_order(oid)
        assert ok is True

    def test_cancel_filled_order_fails(self):
        mgr = self._make_manager()
        order = mgr.place_manual_order("ITC.NS", "BUY", 10, 400.0)
        oid = order["order_id"]

        mgr.update_order_status(oid, "ACK")
        mgr.update_order_status(oid, "FILLED", filled_qty=10, avg_price=400.0)

        ok = mgr.cancel_order(oid)
        assert ok is False  # can't cancel a filled order

    def test_pnl_after_roundtrip(self):
        mgr = self._make_manager()

        # BUY
        buy = mgr.place_manual_order("RELIANCE.NS", "BUY", 10, 2000.0)
        mgr.update_order_status(buy["order_id"], "ACK")
        mgr.update_order_status(
            buy["order_id"], "FILLED", filled_qty=10, avg_price=2000.0
        )

        # SELL at higher price
        sell = mgr.place_manual_order("RELIANCE.NS", "SELL", 10, 2100.0)
        mgr.update_order_status(sell["order_id"], "ACK")
        mgr.update_order_status(
            sell["order_id"], "FILLED", filled_qty=10, avg_price=2100.0
        )

        pnl = mgr.get_pnl()
        # Profit = (2100-2000) * 10 = 1000
        assert pnl["realised_pnl"] == pytest.approx(1000.0)


# ---------------------------------------------------------------------------
# Strategy signal tests
# ---------------------------------------------------------------------------

from app.strategy.strategies import SMACrossoverStrategy, RSIMeanReversionStrategy


class TestSMACrossoverStrategy:
    """Test SMA crossover signal generation."""

    def test_no_signal_insufficient_data(self):
        strat = SMACrossoverStrategy(short_period=3, long_period=5)
        for i in range(4):
            sig = strat.on_tick(
                {"symbol": "TEST.NS", "price": 100 + i, "high": 101 + i, "low": 99 + i}
            )
        assert sig is None  # not enough data yet

    def test_generates_buy_on_crossover(self):
        """Feed a crossover pattern and verify BUY signal."""
        strat = SMACrossoverStrategy(short_period=3, long_period=5)
        # Prices that will cause short SMA to cross above long SMA
        prices = [100, 99, 98, 97, 96, 95, 94, 96, 99, 103, 107, 112]
        signals = []
        for p in prices:
            sig = strat.on_tick(
                {"symbol": "TEST.NS", "price": p, "high": p + 1, "low": p - 1}
            )
            if sig:
                signals.append(sig)
        buy_signals = [s for s in signals if s["action"] == "BUY"]
        assert len(buy_signals) > 0


class TestRSIStrategy:
    """Test RSI mean reversion signals."""

    def test_buy_on_oversold(self):
        strat = RSIMeanReversionStrategy(period=5, oversold=30, overbought=70)
        # Downtrend → should trigger RSI < 30
        prices = [100, 95, 90, 85, 80, 75, 70, 65, 60, 55, 50]
        signals = []
        for p in prices:
            sig = strat.on_tick(
                {"symbol": "TEST.NS", "price": p, "high": p + 1, "low": p - 1}
            )
            if sig:
                signals.append(sig)
        buy_signals = [s for s in signals if s["action"] == "BUY"]
        assert len(buy_signals) > 0
