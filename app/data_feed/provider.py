"""
app/data_feed/provider.py
=========================
Abstract market data provider interface.

Decouples the strategy engine and tick loop from any specific data source.
Switching between Yahoo (demo), Zerodha (live), or any future provider only
requires swapping the provider instance — the consuming code never changes.

Usage::

    provider = YahooProvider()
    df = provider.get_historical("RELIANCE.NS", "1d", 500)
    provider.subscribe_live(["RELIANCE.NS"], on_tick_callback)
"""

import logging
from abc import ABC, abstractmethod
from typing import Callable, Optional

import pandas as pd

logger = logging.getLogger(__name__)


# Type alias for tick callbacks
TickCallback = Callable[[dict], None]


class MarketDataProvider(ABC):
    """
    Abstract interface for all market data sources.

    Concrete providers implement:
        - get_historical(...)  → pd.DataFrame of OHLCV bars
        - subscribe_live(...)  → start streaming ticks for symbols
        - unsubscribe_live()   → stop streaming

    This abstraction ensures the strategy engine, tick loop, and backtester
    never depend on a specific API (Yahoo, Zerodha, Alpaca, etc.).
    """

    @abstractmethod
    def get_historical(
        self,
        symbol: str,
        interval: str = "1d",
        limit: int = 500,
    ) -> pd.DataFrame:
        """Fetch historical OHLCV data for a symbol.

        Parameters
        ----------
        symbol : str
            Ticker symbol (e.g. "RELIANCE.NS").
        interval : str
            Bar interval: "1m", "5m", "15m", "1h", "1d".
        limit : int
            Maximum number of bars to return.

        Returns
        -------
        pd.DataFrame
            Columns: Open, High, Low, Close, Volume with DatetimeIndex.
        """
        ...

    @abstractmethod
    def subscribe_live(
        self,
        symbols: list[str],
        callback: TickCallback,
    ) -> None:
        """Start receiving live ticks for the specified symbols.

        Each tick is delivered as a dict to ``callback``::

            {"symbol": "RELIANCE.NS", "price": 2540.0, "open": ...,
             "high": ..., "low": ..., "close": ..., "volume": ...,
             "timestamp": "2026-02-12T10:30:00"}

        Parameters
        ----------
        symbols : list[str]
            Symbols to subscribe to.
        callback : TickCallback
            Function called for every incoming tick.
        """
        ...

    @abstractmethod
    def unsubscribe_live(self) -> None:
        """Stop all live subscriptions and release resources."""
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable provider name (for logging and UI display)."""
        ...


# =========================================================================
# Yahoo Finance Provider  (used in demo mode)
# =========================================================================


class YahooProvider(MarketDataProvider):
    """
    Demo/backtest data source using Yahoo Finance via ``yfinance``.

    Falls back to synthetic data if Yahoo is blocked or unavailable.
    This provider is safe for demo and paper modes — it never touches
    a real broker.
    """

    def __init__(self):
        # Lazy import so the module loads even if yfinance isn't installed
        self._yf_available = False
        try:
            import yfinance  # noqa: F401

            self._yf_available = True
        except ImportError:
            logger.warning(
                "yfinance not installed — YahooProvider will use synthetic data"
            )

    @property
    def name(self) -> str:
        return "yahoo"

    def get_historical(
        self,
        symbol: str,
        interval: str = "1d",
        limit: int = 500,
    ) -> pd.DataFrame:
        """Download OHLCV from Yahoo Finance.  Falls back to synthetic data."""
        from app.utils.data import download_ohlcv, load_cached_ohlcv, resolve_symbol

        resolved = resolve_symbol(symbol)
        # Try cached first
        df = load_cached_ohlcv(resolved, interval)
        if not df.empty and len(df) >= min(limit, 50):
            return df.tail(limit)

        # Download (with automatic synthetic fallback)
        df = download_ohlcv(resolved, period="2y", interval=interval, save=True)
        return df.tail(limit) if not df.empty else df

    def subscribe_live(
        self,
        symbols: list[str],
        callback: TickCallback,
    ) -> None:
        """Yahoo does not offer real-time websockets.

        For demo mode, the DemoDataFeed handles tick simulation.
        This method is a no-op; live streaming is handled by DemoDataFeed.
        """
        logger.info(
            "YahooProvider.subscribe_live called — "
            "live ticks are handled by DemoDataFeed in demo mode"
        )

    def unsubscribe_live(self) -> None:
        pass  # No-op for Yahoo


# =========================================================================
# Zerodha Kite Provider  (placeholder for live mode)
# =========================================================================


class ZerodhaProvider(MarketDataProvider):
    """
    Placeholder for Zerodha Kite Connect integration.

    Prerequisites (not included in this demo):
        pip install kiteconnect
        Obtain api_key and access_token from https://kite.trade

    When implementing:
    1. Use KiteConnect.historical_data() for get_historical()
    2. Use KiteTicker for subscribe_live()
    3. Map NSE symbols: "RELIANCE.NS" → "RELIANCE" exchange="NSE"
    """

    def __init__(self, api_key: str = "", access_token: str = ""):
        self._api_key = api_key
        self._access_token = access_token
        self._kite = None
        self._ticker = None
        logger.info("ZerodhaProvider initialised (placeholder — not connected)")

    @property
    def name(self) -> str:
        return "zerodha"

    def get_historical(
        self,
        symbol: str,
        interval: str = "1d",
        limit: int = 500,
    ) -> pd.DataFrame:
        """Fetch OHLCV from Zerodha Kite historical data API.

        Implementation sketch::

            from kiteconnect import KiteConnect
            kite = KiteConnect(api_key=self._api_key)
            kite.set_access_token(self._access_token)
            instrument_token = kite.ltp(f"NSE:{symbol}")
            data = kite.historical_data(instrument_token, from_date, to_date, interval)
            return pd.DataFrame(data)
        """
        raise NotImplementedError(
            "ZerodhaProvider.get_historical() is a placeholder. "
            "Wire KiteConnect.historical_data() here."
        )

    def subscribe_live(
        self,
        symbols: list[str],
        callback: TickCallback,
    ) -> None:
        """Subscribe to Zerodha KiteTicker for live market data.

        Implementation sketch::

            from kiteconnect import KiteTicker
            self._ticker = KiteTicker(self._api_key, self._access_token)
            self._ticker.on_ticks = lambda ws, ticks: [callback(t) for t in ticks]
            self._ticker.subscribe(instrument_tokens)
            self._ticker.set_mode(self._ticker.MODE_FULL, instrument_tokens)
            self._ticker.connect(threaded=True)
        """
        raise NotImplementedError(
            "ZerodhaProvider.subscribe_live() is a placeholder. "
            "Wire KiteTicker here."
        )

    def unsubscribe_live(self) -> None:
        if self._ticker:
            self._ticker.close()
            self._ticker = None


# =========================================================================
# Factory function — instantiate the right provider based on MODE
# =========================================================================


def create_provider(mode: str = "demo") -> MarketDataProvider:
    """Create and return the appropriate data provider for the given mode.

    Parameters
    ----------
    mode : str
        "demo"  → YahooProvider (synthetic/cached data)
        "paper" → YahooProvider (real Yahoo data, simulated execution)
        "live"  → ZerodhaProvider (real data + real broker)
    """
    if mode in ("demo", "paper"):
        logger.info("Creating YahooProvider for mode=%s", mode)
        return YahooProvider()
    elif mode == "live":
        logger.info("Creating ZerodhaProvider for mode=%s", mode)
        return ZerodhaProvider()
    else:
        logger.warning("Unknown mode '%s' — defaulting to YahooProvider", mode)
        return YahooProvider()
