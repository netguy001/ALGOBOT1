"""
app/data_feed/demo_feed.py
==========================
Demo data feed that replays OHLCV bars from CSV as simulated ticks.

Wraps the existing ``tick_generator`` infrastructure behind the abstract
``DataFeed`` interface so the rest of the system never depends on Yahoo
Finance or file I/O directly.
"""

import logging
import threading
import time
from typing import Optional

from app.data_feed.base import DataFeed, TickCallback
from app.utils.data import (
    tick_generator,
    resolve_symbol,
    download_ohlcv,
    load_cached_ohlcv,
)

logger = logging.getLogger(__name__)


class DemoDataFeed(DataFeed):
    """
    Replay historical OHLCV data as a simulated live tick stream.

    Each CSV row becomes one tick. The replay runs in a background thread
    controlled by ``connect()`` / ``disconnect()``.
    """

    def __init__(
        self,
        symbols: list[str],
        tick_interval: float = 0.5,
        loop: bool = True,
    ):
        """
        Parameters
        ----------
        symbols : list[str]
            Initial symbols to subscribe to (e.g. ``["RELIANCE.NS", "TCS.NS"]``).
        tick_interval : float
            Seconds between ticks *within* a cycle (inter-symbol delay).
        loop : bool
            Whether to wrap around when CSV data is exhausted.
        """
        self._symbols: list[str] = list(symbols)
        self._tick_interval = tick_interval
        self._loop = loop

        self._callbacks: list[TickCallback] = []
        self._connected = False
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------
    # DataFeed interface
    # ------------------------------------------------------------------

    def connect(self) -> None:
        if self._connected:
            logger.warning("DemoDataFeed already connected")
            return

        # Ensure data exists for all symbols
        for sym in self._symbols:
            df = load_cached_ohlcv(sym)
            if df.empty:
                logger.info("Downloading data for %s ...", sym)
                download_ohlcv(sym)

        self._stop_event.clear()
        self._connected = True
        logger.info("DemoDataFeed connected — symbols=%s", self._symbols)

    def disconnect(self) -> None:
        self._stop_event.set()
        self._connected = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        logger.info("DemoDataFeed disconnected")

    def subscribe(self, symbol: str) -> None:
        resolved = resolve_symbol(symbol)
        if resolved not in self._symbols:
            self._symbols.append(resolved)
            logger.info("Subscribed to %s", resolved)

    def unsubscribe(self, symbol: str) -> None:
        resolved = resolve_symbol(symbol)
        if resolved in self._symbols:
            self._symbols.remove(resolved)
            logger.info("Unsubscribed from %s", resolved)

    def on_tick(self, callback: TickCallback) -> None:
        self._callbacks.append(callback)

    def get_symbols(self) -> list[str]:
        return list(self._symbols)

    @property
    def is_connected(self) -> bool:
        return self._connected

    # ------------------------------------------------------------------
    # Tick generation
    # ------------------------------------------------------------------

    def create_generators(self) -> dict:
        """
        Create tick generators for all subscribed symbols.

        Returns a dict mapping resolved_symbol -> generator.
        Called by the consumer (e.g. main.py tick loop) to get generators
        that yield tick dicts.
        """
        generators = {}
        for sym in self._symbols:
            resolved = resolve_symbol(sym)
            generators[resolved] = tick_generator(sym, interval_sec=0)
        return generators

    @property
    def should_stop(self) -> bool:
        return self._stop_event.is_set()
