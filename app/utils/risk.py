"""
app/utils/risk.py
=================
Risk-management utilities: position sizing, stop-loss / take-profit
calculation, and drawdown tracking.
"""

import logging
from dataclasses import dataclass, field
from typing import Optional

from app.config import (
    DEFAULT_STOP_LOSS_PCT,
    DEFAULT_TAKE_PROFIT_PCT,
    INITIAL_CAPITAL,
    RISK_PER_TRADE_PCT,
)

logger = logging.getLogger(__name__)


@dataclass
class RiskParams:
    """Per-trade risk parameters (all percentages are 0-100 scale)."""

    capital: float = INITIAL_CAPITAL
    risk_pct: float = RISK_PER_TRADE_PCT  # e.g. 1.0 means 1 %
    stop_loss_pct: float = DEFAULT_STOP_LOSS_PCT  # e.g. 2.0 means 2 %
    take_profit_pct: float = DEFAULT_TAKE_PROFIT_PCT


def position_size(price: float, params: Optional[RiskParams] = None) -> int:
    """
    Compute the number of shares to buy so that a stop-loss hit loses
    at most ``risk_pct`` of ``capital``.

    Formula::

        risk_amount   = capital × (risk_pct / 100)
        risk_per_share = price × (stop_loss_pct / 100)
        qty           = floor(risk_amount / risk_per_share)

    Returns at least 1 share (min order).
    """
    if params is None:
        params = RiskParams()
    risk_amount = params.capital * (params.risk_pct / 100)
    risk_per_share = price * (params.stop_loss_pct / 100)
    if risk_per_share <= 0:
        logger.error("risk_per_share is zero — check stop_loss_pct")
        return 1
    qty = int(risk_amount / risk_per_share)
    return max(qty, 1)


def stop_loss_price(
    entry_price: float, side: str = "BUY", pct: Optional[float] = None
) -> float:
    """
    Compute the stop-loss price.

    For BUY orders the SL is *below* entry; for SELL (short) it is *above*.
    """
    pct = pct if pct is not None else DEFAULT_STOP_LOSS_PCT
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
