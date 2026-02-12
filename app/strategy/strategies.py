"""
app/strategy/strategies.py
==========================
Concrete trading strategy implementations.

Each strategy class follows the same interface:
    .on_tick(tick: dict) -> Optional[Signal]

A ``Signal`` is a simple dict:
    {"action": "BUY"|"SELL", "symbol": str, "price": float, "reason": str}

Production improvements over demo version:
    - Trend filter: BUY only when price > SMA(50), SELL only when price < SMA(50).
    - Signals fire only on actual crossover events, not continuous conditions.
    - Per-symbol state tracking prevents duplicate signals.
"""

import logging
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

from app.utils.indicators import sma, rsi, momentum, donchian_channel
from app.config import (
    SMA_SHORT,
    SMA_LONG,
    RSI_PERIOD,
    RSI_OVERSOLD,
    RSI_OVERBOUGHT,
)

logger = logging.getLogger(__name__)

Signal = dict  # type alias for clarity

# Trend-filter period used across all strategies
_TREND_SMA_PERIOD = 50


@dataclass
class _PriceBuffer:
    """Rolling buffer of close prices used to compute indicators on the fly."""

    maxlen: int = 200
    prices: list[float] = field(default_factory=list)
    highs: list[float] = field(default_factory=list)
    lows: list[float] = field(default_factory=list)

    def append(self, tick: dict) -> None:
        self.prices.append(tick["price"])
        self.highs.append(tick.get("high", tick["price"]))
        self.lows.append(tick.get("low", tick["price"]))
        if len(self.prices) > self.maxlen:
            self.prices = self.prices[-self.maxlen :]
            self.highs = self.highs[-self.maxlen :]
            self.lows = self.lows[-self.maxlen :]

    @property
    def series(self) -> pd.Series:
        return pd.Series(self.prices)

    @property
    def high_series(self) -> pd.Series:
        return pd.Series(self.highs)

    @property
    def low_series(self) -> pd.Series:
        return pd.Series(self.lows)

    def __len__(self) -> int:
        return len(self.prices)


def _trend_filter(buf: _PriceBuffer, action: str, price: float) -> bool:
    """
    Apply trend filter: only allow BUY when price > SMA50,
    only allow SELL when price < SMA50.

    Returns True if the signal is ALLOWED, False if blocked.
    If not enough data yet, permit the signal (warm-up phase).
    """
    if len(buf) < _TREND_SMA_PERIOD + 1:
        return True  # allow during warm-up
    sma50_val = sma(buf.series, _TREND_SMA_PERIOD).iloc[-1]
    if pd.isna(sma50_val):
        return True
    if action == "BUY" and price <= sma50_val:
        return False
    if action == "SELL" and price >= sma50_val:
        return False
    return True


# =========================================================================
# SMA Crossover Strategy
# =========================================================================


class SMACrossoverStrategy:
    """
    Buy when SMA(short) crosses above SMA(long).
    Sell when SMA(short) crosses below SMA(long).

    Fires ONLY on the crossover event (edge-trigger, not level-trigger).
    Includes trend filter: BUY only above SMA50, SELL only below SMA50.
    """

    name = "sma_crossover"

    def __init__(self, short_period: int = SMA_SHORT, long_period: int = SMA_LONG):
        self.short_period = short_period
        self.long_period = long_period
        buf_len = max(long_period, _TREND_SMA_PERIOD) + 10
        self._buf = _PriceBuffer(maxlen=buf_len)
        self._prev_short: Optional[float] = None
        self._prev_long: Optional[float] = None

    def on_tick(self, tick: dict) -> Optional[Signal]:
        self._buf.append(tick)
        if len(self._buf) < self.long_period + 1:
            return None

        s = sma(self._buf.series, self.short_period)
        l = sma(self._buf.series, self.long_period)
        cur_short, cur_long = s.iloc[-1], l.iloc[-1]

        signal = None
        if self._prev_short is not None and self._prev_long is not None:
            # Golden cross (edge-trigger: previous was below, now above)
            if self._prev_short <= self._prev_long and cur_short > cur_long:
                if _trend_filter(self._buf, "BUY", tick["price"]):
                    signal = {
                        "action": "BUY",
                        "symbol": tick["symbol"],
                        "price": tick["price"],
                        "reason": f"SMA{self.short_period} crossed above SMA{self.long_period}",
                    }
            # Death cross
            elif self._prev_short >= self._prev_long and cur_short < cur_long:
                if _trend_filter(self._buf, "SELL", tick["price"]):
                    signal = {
                        "action": "SELL",
                        "symbol": tick["symbol"],
                        "price": tick["price"],
                        "reason": f"SMA{self.short_period} crossed below SMA{self.long_period}",
                    }

        self._prev_short = cur_short
        self._prev_long = cur_long
        return signal

    def reset(self) -> None:
        buf_len = max(self.long_period, _TREND_SMA_PERIOD) + 10
        self._buf = _PriceBuffer(maxlen=buf_len)
        self._prev_short = None
        self._prev_long = None


# =========================================================================
# RSI Mean-Reversion Strategy
# =========================================================================


class RSIMeanReversionStrategy:
    """
    Buy when RSI drops below ``oversold`` threshold.
    Sell when RSI rises above ``overbought`` threshold.

    Only fires ONCE per zone entry. Resets when RSI returns to neutral.
    Includes trend filter.
    """

    name = "rsi_mean_reversion"

    def __init__(
        self,
        period: int = RSI_PERIOD,
        oversold: int = RSI_OVERSOLD,
        overbought: int = RSI_OVERBOUGHT,
    ):
        self.period = period
        self.oversold = oversold
        self.overbought = overbought
        buf_len = max(period + 20, _TREND_SMA_PERIOD + 10)
        self._buf = _PriceBuffer(maxlen=buf_len)
        # Track per-symbol whether we already fired in the current zone
        self._fired: dict[str, str] = {}  # symbol -> "BUY" | "SELL" | ""

    def on_tick(self, tick: dict) -> Optional[Signal]:
        self._buf.append(tick)
        if len(self._buf) < self.period + 2:
            return None

        sym = tick["symbol"]
        rsi_vals = rsi(self._buf.series, self.period)
        cur_rsi = rsi_vals.iloc[-1]

        prev_fired = self._fired.get(sym, "")

        if cur_rsi < self.oversold:
            if prev_fired != "BUY":
                if _trend_filter(self._buf, "BUY", tick["price"]):
                    self._fired[sym] = "BUY"
                    return {
                        "action": "BUY",
                        "symbol": sym,
                        "price": tick["price"],
                        "reason": f"RSI({self.period})={cur_rsi:.1f} < {self.oversold} (oversold)",
                    }
        elif cur_rsi > self.overbought:
            if prev_fired != "SELL":
                if _trend_filter(self._buf, "SELL", tick["price"]):
                    self._fired[sym] = "SELL"
                    return {
                        "action": "SELL",
                        "symbol": sym,
                        "price": tick["price"],
                        "reason": f"RSI({self.period})={cur_rsi:.1f} > {self.overbought} (overbought)",
                    }
        else:
            # RSI is in neutral zone — reset the fired flag
            self._fired[sym] = ""
        return None

    def reset(self) -> None:
        buf_len = max(self.period + 20, _TREND_SMA_PERIOD + 10)
        self._buf = _PriceBuffer(maxlen=buf_len)
        self._fired.clear()


# =========================================================================
# Breakout Strategy
# =========================================================================


class BreakoutStrategy:
    """
    Buy on a Donchian Channel upper-band breakout.
    Sell on a lower-band breakdown.

    Includes trend filter.
    """

    name = "breakout"

    def __init__(self, period: int = 20):
        self.period = period
        buf_len = max(period + 10, _TREND_SMA_PERIOD + 10)
        self._buf = _PriceBuffer(maxlen=buf_len)

    def on_tick(self, tick: dict) -> Optional[Signal]:
        self._buf.append(tick)
        if len(self._buf) < self.period + 1:
            return None

        upper, lower = donchian_channel(
            self._buf.high_series, self._buf.low_series, self.period
        )
        price = tick["price"]

        if price > upper.iloc[-2]:  # breakout above prior upper band
            if _trend_filter(self._buf, "BUY", price):
                return {
                    "action": "BUY",
                    "symbol": tick["symbol"],
                    "price": price,
                    "reason": f"Price {price:.2f} broke above Donchian({self.period}) upper {upper.iloc[-2]:.2f}",
                }
        elif price < lower.iloc[-2]:
            if _trend_filter(self._buf, "SELL", price):
                return {
                    "action": "SELL",
                    "symbol": tick["symbol"],
                    "price": price,
                    "reason": f"Price {price:.2f} broke below Donchian({self.period}) lower {lower.iloc[-2]:.2f}",
                }
        return None

    def reset(self) -> None:
        buf_len = max(self.period + 10, _TREND_SMA_PERIOD + 10)
        self._buf = _PriceBuffer(maxlen=buf_len)


# =========================================================================
# Momentum Strategy
# =========================================================================


class MomentumStrategy:
    """
    Buy when momentum (price change over ``period`` bars) turns positive.
    Sell when it turns negative.

    Edge-trigger only (fires on zero-crossing, not continuous positive/negative).
    Includes trend filter.
    """

    name = "momentum"

    def __init__(self, period: int = 10):
        self.period = period
        buf_len = max(period + 10, _TREND_SMA_PERIOD + 10)
        self._buf = _PriceBuffer(maxlen=buf_len)
        self._prev_mom: Optional[float] = None

    def on_tick(self, tick: dict) -> Optional[Signal]:
        self._buf.append(tick)
        if len(self._buf) < self.period + 2:
            return None

        mom = momentum(self._buf.series, self.period)
        cur_mom = mom.iloc[-1]
        signal = None

        if self._prev_mom is not None:
            if self._prev_mom <= 0 and cur_mom > 0:
                if _trend_filter(self._buf, "BUY", tick["price"]):
                    signal = {
                        "action": "BUY",
                        "symbol": tick["symbol"],
                        "price": tick["price"],
                        "reason": f"Momentum({self.period}) turned positive: {cur_mom:.2f}",
                    }
            elif self._prev_mom >= 0 and cur_mom < 0:
                if _trend_filter(self._buf, "SELL", tick["price"]):
                    signal = {
                        "action": "SELL",
                        "symbol": tick["symbol"],
                        "price": tick["price"],
                        "reason": f"Momentum({self.period}) turned negative: {cur_mom:.2f}",
                    }

        self._prev_mom = cur_mom
        return signal

    def reset(self) -> None:
        buf_len = max(self.period + 10, _TREND_SMA_PERIOD + 10)
        self._buf = _PriceBuffer(maxlen=buf_len)
        self._prev_mom = None


# =========================================================================
# Registry — used by the strategy engine to look up strategies by name
# =========================================================================

STRATEGY_REGISTRY: dict[str, type] = {
    "sma_crossover": SMACrossoverStrategy,
    "rsi_mean_reversion": RSIMeanReversionStrategy,
    "breakout": BreakoutStrategy,
    "momentum": MomentumStrategy,
}
