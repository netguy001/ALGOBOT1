"""
app/utils/candle_aggregator.py
==============================
Timeframe-aware candle aggregator.

Aggregates raw ticks into OHLCV candles aligned to exact timeframe
boundaries (e.g. 20:00, 20:05, 20:10 for 5m candles).

Usage::

    from app.utils.candle_aggregator import CandleAggregator

    agg = CandleAggregator(clock=engine_clock, timeframe="5m")

    # In the tick loop:
    completed = agg.on_tick(symbol, price, volume)
    if completed:
        # A candle just closed — persist / broadcast it
        storage.upsert_candle(completed)

Architecture:
    - Ticks are bucketed by ``floor(epoch / tf_seconds) * tf_seconds``
    - Each bucket accumulates OHLCV in memory
    - When a tick arrives for a NEW boundary, the previous candle is
      "completed" and returned for persistence/broadcast
    - Thread-safe via per-symbol locks
"""

import logging
import threading
from typing import Optional

from app.utils.clock import EngineClock, TIMEFRAME_SECONDS

logger = logging.getLogger(__name__)


class CandleAggregator:
    """
    Accumulates ticks into timeframe-aligned candles.

    One instance per engine — handles all symbols.
    """

    def __init__(self, clock: EngineClock, timeframe: str = "1m"):
        """
        Parameters
        ----------
        clock : EngineClock
            Authoritative time source.
        timeframe : str
            Candle timeframe (``1m``, ``5m``, ``15m``, ``1h``, etc.).
        """
        self._clock = clock
        self._timeframe = timeframe
        self._tf_seconds = TIMEFRAME_SECONDS.get(timeframe, 60)
        self._lock = threading.Lock()

        # Per-symbol in-progress candle:
        #   { symbol: {"boundary": int, "open": f, "high": f, "low": f,
        #              "close": f, "volume": f} }
        self._current: dict[str, dict] = {}

    @property
    def timeframe(self) -> str:
        return self._timeframe

    def on_tick(
        self,
        symbol: str,
        price: float,
        volume: float = 0,
        tick_epoch: Optional[int] = None,
    ) -> Optional[dict]:
        """
        Feed a tick into the aggregator.

        Parameters
        ----------
        symbol : str
            Instrument symbol (e.g. ``RELIANCE.NS``)
        price : float
            Trade / tick price
        volume : float
            Tick volume (additive within a candle)
        tick_epoch : int | None
            Override epoch for the tick.  Defaults to ``clock.epoch()``.

        Returns
        -------
        dict | None
            If this tick crosses into a new candle boundary, the
            **completed** candle dict is returned (ready for DB persistence).
            Otherwise ``None``.
        """
        if tick_epoch is None:
            tick_epoch = self._clock.epoch()

        boundary = self._clock.candle_boundary(self._timeframe, tick_epoch)

        with self._lock:
            current = self._current.get(symbol)

            # First tick for this symbol — start a new candle
            if current is None:
                self._current[symbol] = self._new_candle(boundary, price, volume)
                return None

            # Same candle period — update OHLCV
            if current["boundary"] == boundary:
                current["high"] = max(current["high"], price)
                current["low"] = min(current["low"], price)
                current["close"] = price
                current["volume"] += volume
                return None

            # New boundary — complete the old candle, start new one
            completed = self._finalize(symbol, current)
            self._current[symbol] = self._new_candle(boundary, price, volume)
            return completed

    def flush(self, symbol: str) -> Optional[dict]:
        """
        Force-close the current candle for a symbol (e.g. at market close).

        Returns the completed candle dict or None.
        """
        with self._lock:
            current = self._current.pop(symbol, None)
            if current is not None:
                return self._finalize(symbol, current)
        return None

    def flush_all(self) -> list[dict]:
        """Force-close all in-progress candles.  Returns list of completed candles."""
        results = []
        with self._lock:
            for symbol in list(self._current.keys()):
                current = self._current.pop(symbol)
                results.append(self._finalize(symbol, current))
        return results

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _new_candle(boundary: int, price: float, volume: float) -> dict:
        """Create a fresh in-progress candle."""
        return {
            "boundary": boundary,
            "open": price,
            "high": price,
            "low": price,
            "close": price,
            "volume": volume,
        }

    def _finalize(self, symbol: str, candle: dict) -> dict:
        """Convert an in-progress candle to a completed candle dict."""
        return {
            "symbol": symbol,
            "timeframe": self._timeframe,
            "timestamp": candle["boundary"],
            "open": round(candle["open"], 2),
            "high": round(candle["high"], 2),
            "low": round(candle["low"], 2),
            "close": round(candle["close"], 2),
            "volume": round(candle["volume"], 2),
        }
