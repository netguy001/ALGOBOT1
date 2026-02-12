"""
app/utils/risk.py
=================
Risk-management utilities: position sizing, stop-loss / take-profit
calculation, and drawdown tracking.

Safety features:
    - Minimum stop-loss distance (MIN_STOP_LOSS_PCT) prevents division-by-
      near-zero and quantity explosion.
    - MIN_STOP_DISTANCE_PCT — if the computed stop distance is below this
      floor the trade is REJECTED outright (not just clamped).
    - MAX_POSITION_SIZE_PCT_OF_CAPITAL — caps max notional at N% of capital.
    - ABSOLUTE_MAX_QTY — hard ceiling on share count regardless of capital.
    - Hard cap on max quantity per order (MAX_QTY_PER_ORDER).
    - Division-by-zero guards on all calculations.
"""

import logging
from dataclasses import dataclass, field
from typing import Optional

from app.config import (
    DEFAULT_STOP_LOSS_PCT,
    DEFAULT_TAKE_PROFIT_PCT,
    INITIAL_CAPITAL,
    RISK_PER_TRADE_PCT,
    MIN_STOP_LOSS_PCT,
    MAX_QTY_PER_ORDER,
    MAX_POSITION_SIZE_PER_TRADE,
    MIN_STOP_DISTANCE_PCT,
    MAX_POSITION_SIZE_PCT_OF_CAPITAL,
    ABSOLUTE_MAX_QTY,
)

logger = logging.getLogger(__name__)


@dataclass
class RiskParams:
    """Per-trade risk parameters (all percentages are 0-100 scale)."""

    capital: float = INITIAL_CAPITAL
    risk_pct: float = RISK_PER_TRADE_PCT  # e.g. 1.0 means 1 %
    stop_loss_pct: float = DEFAULT_STOP_LOSS_PCT  # e.g. 2.0 means 2 %
    take_profit_pct: float = DEFAULT_TAKE_PROFIT_PCT


def validate_stop_distance(stop_loss_pct: float) -> bool:
    """Return True if the stop-loss distance meets the minimum threshold.

    Guard: prevents trades where the SL is so tight that tiny noise would
    trigger a stop, AND prevents extremely large position sizes caused by
    dividing risk-amount by a near-zero stop distance.
    """
    return stop_loss_pct >= MIN_STOP_DISTANCE_PCT


def position_size(
    price: float,
    params: Optional[RiskParams] = None,
    max_qty: int = MAX_QTY_PER_ORDER,
    max_position_notional: float = 0.0,
) -> int:
    """
    Compute the number of shares to buy so that a stop-loss hit loses
    at most ``risk_pct`` of ``capital``.

    Safety guards (applied in order):
        1. SL% must meet MIN_STOP_DISTANCE_PCT — returns 0 if below.
        2. SL% is floored at MIN_STOP_LOSS_PCT (prevents near-zero SL).
        3. Notional capped at MAX_POSITION_SIZE_PCT_OF_CAPITAL.
        4. Result capped at MAX_POSITION_SIZE_PER_TRADE.
        5. Result capped at ``max_qty`` (per-order limit).
        6. Result capped at ABSOLUTE_MAX_QTY (hard ceiling).
        7. qty * price must not exceed available capital.

    Formula::

        risk_amount    = capital * (risk_pct / 100)
        risk_per_share = price   * (stop_loss_pct / 100)
        qty            = floor(risk_amount / risk_per_share)
    """
    if params is None:
        params = RiskParams()

    if price <= 0:
        logger.warning("position_size called with price=%.4f — returning 1", price)
        return 1

    # Guard 1: reject if stop distance below minimum threshold
    if not validate_stop_distance(params.stop_loss_pct):
        logger.warning(
            "Trade rejected: stop_loss_pct=%.2f%% < min_stop_distance=%.2f%%",
            params.stop_loss_pct,
            MIN_STOP_DISTANCE_PCT,
        )
        return 0

    # Guard 2: floor the SL percentage to prevent explosion
    effective_sl_pct = max(params.stop_loss_pct, MIN_STOP_LOSS_PCT)

    risk_amount = params.capital * (params.risk_pct / 100)
    risk_per_share = price * (effective_sl_pct / 100)

    if risk_per_share <= 0:
        logger.error("risk_per_share is zero after floor — returning 1")
        return 1

    qty = int(risk_amount / risk_per_share)

    # Guard 3: notional cap as % of capital
    # Prevents any single position from being too large relative to total capital
    max_notional_by_pct = params.capital * (MAX_POSITION_SIZE_PCT_OF_CAPITAL / 100)
    if price > 0:
        qty = min(qty, int(max_notional_by_pct / price))

    # Guard 4: per-trade share count cap
    qty = min(qty, MAX_POSITION_SIZE_PER_TRADE)

    # Guard 5: per-order cap
    qty = min(qty, max_qty)

    # Guard 6: absolute hard ceiling on quantity
    qty = min(qty, ABSOLUTE_MAX_QTY)

    # Guard 7: ensure qty * price does not exceed available capital
    if params.capital > 0 and price > 0:
        max_by_capital = int(params.capital / price)
        qty = min(qty, max_by_capital)

    # Guard 8: external notional cap if provided
    if max_position_notional > 0 and price > 0:
        qty = min(qty, int(max_position_notional / price))

    return max(qty, 0)  # never return negative; 0 means trade rejected


def stop_loss_price(
    entry_price: float, side: str = "BUY", pct: Optional[float] = None
) -> float:
    """
    Compute the stop-loss price.

    For BUY orders the SL is *below* entry; for SELL (short) it is *above*.
    The SL% is floored at MIN_STOP_LOSS_PCT to prevent near-zero SL distance.
    """
    pct = pct if pct is not None else DEFAULT_STOP_LOSS_PCT
    pct = max(pct, MIN_STOP_LOSS_PCT)  # enforce minimum distance
    if entry_price <= 0:
        return 0.0
    offset = entry_price * (pct / 100)
    if side.upper() == "BUY":
        return round(entry_price - offset, 2)
    return round(entry_price + offset, 2)


def take_profit_price(
    entry_price: float, side: str = "BUY", pct: Optional[float] = None
) -> float:
    """Compute the take-profit target price."""
    pct = pct if pct is not None else DEFAULT_TAKE_PROFIT_PCT
    offset = entry_price * (pct / 100)
    if side.upper() == "BUY":
        return round(entry_price + offset, 2)
    return round(entry_price - offset, 2)


# ---------------------------------------------------------------------------
# Drawdown tracker (meant to be used as a singleton per session)
# ---------------------------------------------------------------------------


@dataclass
class DrawdownTracker:
    """Track running drawdown during a session or backtest."""

    peak_capital: float = INITIAL_CAPITAL
    current_capital: float = INITIAL_CAPITAL
    max_drawdown_pct: float = 0.0
    _history: list[float] = field(default_factory=list)

    def update(self, current_capital: float) -> float:
        """
        Update with the latest capital value and return the current drawdown %.
        """
        self.current_capital = current_capital
        self._history.append(current_capital)
        if current_capital > self.peak_capital:
            self.peak_capital = current_capital
        dd = 0.0
        if self.peak_capital > 0:
            dd = ((self.peak_capital - current_capital) / self.peak_capital) * 100
        if dd > self.max_drawdown_pct:
            self.max_drawdown_pct = dd
        return dd

    def reset(self) -> None:
        self.peak_capital = INITIAL_CAPITAL
        self.current_capital = INITIAL_CAPITAL
        self.max_drawdown_pct = 0.0
        self._history.clear()
