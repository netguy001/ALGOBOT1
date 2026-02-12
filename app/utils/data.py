"""
app/utils/data.py
=================
Yahoo Finance data helper, synthetic fallback generator, and tick simulator
for Indian stocks (NSE/BSE).

Ticker format notes
-------------------
* NSE tickers use the ``.NS`` suffix  → ``RELIANCE.NS``, ``TCS.NS``
* BSE tickers use the ``.BO`` suffix  → ``RELIANCE.BO``
* Some scrips have non-obvious Yahoo names — always verify via
  https://finance.yahoo.com/lookup

yfinance limits
---------------
* Daily data: available for 20+ years.
* 1-minute intraday: last 7 days only.
* Rate limit: ~2 000 requests / hour (unauthed).
* Yahoo Finance API can be unreliable/blocked — synthetic fallback is provided.
"""

import logging
import math
import random
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Generator, Optional

import numpy as np
import pandas as pd

from app.config import DATA_DIR, DEFAULT_SYMBOLS, TICK_INTERVAL_SEC

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Try to import yfinance; if absent or broken we rely on synthetic data
# ---------------------------------------------------------------------------
try:
    import yfinance as yf

    _YF_AVAILABLE = True
except ImportError:
    _YF_AVAILABLE = False
    logger.warning("yfinance not installed — will use synthetic data only")

# NSE ticker mapping — add entries here when Yahoo name differs
SYMBOL_MAP: dict[str, str] = {
    "RELIANCE": "RELIANCE.NS",
    "TCS": "TCS.NS",
    "INFY": "INFY.NS",
    "HDFCBANK": "HDFCBANK.NS",
    "ICICIBANK": "ICICIBANK.NS",
    "SBIN": "SBIN.NS",
    "BHARTIARTL": "BHARTIARTL.NS",
    "ITC": "ITC.NS",
    "KOTAKBANK": "KOTAKBANK.NS",
    "LT": "LT.NS",
}

# Realistic base prices for Indian large-caps (used by synthetic generator)
_SYNTHETIC_BASE_PRICES: dict[str, float] = {
    "RELIANCE.NS": 2540.0,
    "TCS.NS": 3850.0,
    "INFY.NS": 1620.0,
    "HDFCBANK.NS": 1720.0,
    "ICICIBANK.NS": 1150.0,
    "SBIN.NS": 620.0,
    "BHARTIARTL.NS": 1480.0,
    "ITC.NS": 440.0,
    "KOTAKBANK.NS": 1810.0,
    "LT.NS": 3550.0,
}


def resolve_symbol(symbol: str) -> str:
    """Return Yahoo-compatible ticker.  Append .NS if suffix missing."""
    if symbol in SYMBOL_MAP:
        return SYMBOL_MAP[symbol]
    if not (symbol.endswith(".NS") or symbol.endswith(".BO")):
        return f"{symbol}.NS"
    return symbol


# ---------------------------------------------------------------------------
# Synthetic OHLCV generator  (fallback when yfinance fails)
# ---------------------------------------------------------------------------


def generate_synthetic_ohlcv(
    symbol: str,
    days: int = 500,
    save: bool = True,
) -> pd.DataFrame:
    """
    Generate realistic synthetic daily OHLCV data for an Indian stock.

    Uses geometric Brownian motion with mean-reverting tendency, so the
    resulting chart looks plausible for a demo.  Volatility and drift are
    calibrated for Indian large-caps (~1.5 % daily vol, ~12 % annual drift).

    Parameters
    ----------
    symbol : str   Yahoo-style ticker
    days   : int   Number of trading days to generate
    save   : bool  Write CSV to DATA_DIR

    Returns
    -------
    pd.DataFrame  with columns Open, High, Low, Close, Volume and DatetimeIndex
    """
    yf_symbol = resolve_symbol(symbol)
    base_price = _SYNTHETIC_BASE_PRICES.get(yf_symbol, 1500.0)

    # Seed for reproducibility per-symbol so re-runs give the same data
    seed = sum(ord(c) for c in yf_symbol)
    rng = np.random.RandomState(seed)

    # GBM parameters
    annual_drift = 0.12  # ~12 % annual return
    annual_vol = 0.25  # ~25 % annual volatility
    dt = 1 / 252  # one trading day
    daily_drift = annual_drift * dt
    daily_vol = annual_vol * math.sqrt(dt)

    # Generate log-returns
    log_returns = rng.normal(daily_drift, daily_vol, days)

    # Build close prices
    closes = np.zeros(days)
    closes[0] = base_price
    for i in range(1, days):
        closes[i] = closes[i - 1] * math.exp(log_returns[i])

    # Derive OHLV from Close
    intraday_range = rng.uniform(0.005, 0.025, days)  # 0.5–2.5 % range
    opens = closes * (1 + rng.uniform(-0.005, 0.005, days))
    highs = np.maximum(opens, closes) * (1 + intraday_range / 2)
    lows = np.minimum(opens, closes) * (1 - intraday_range / 2)
    volumes = (rng.lognormal(mean=14, sigma=0.6, size=days)).astype(int)

    # Build DatetimeIndex (last N trading days ending today)
    end_date = datetime.now()
    dates = pd.bdate_range(end=end_date, periods=days)

    df = pd.DataFrame(
        {
            "Open": np.round(opens, 2),
            "High": np.round(highs, 2),
            "Low": np.round(lows, 2),
            "Close": np.round(closes, 2),
            "Volume": volumes,
        },
        index=dates,
    )
    df.index.name = "Date"

    if save:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        path = DATA_DIR / f"{yf_symbol.replace('.', '_')}_1d.csv"
        df.to_csv(path)
        logger.info("Generated %d rows of synthetic data → %s", len(df), path)

    return df


# ---------------------------------------------------------------------------
# Download & cache (with automatic synthetic fallback)
# ---------------------------------------------------------------------------


def download_ohlcv(
    symbol: str,
    period: str = "2y",
    interval: str = "1d",
    save: bool = True,
) -> pd.DataFrame:
    """
    Download OHLCV from Yahoo Finance.  Falls back to synthetic data if
    yfinance is unavailable or the download fails.

    Parameters
    ----------
    symbol : str   Yahoo-format ticker (e.g. ``RELIANCE.NS``)
    period : str   yfinance period string (``1y``, ``2y``, ``max``, …)
    interval : str yfinance interval (``1d``, ``1h``, ``5m``, …)
    save : bool    persist to CSV under DATA_DIR

    Returns
    -------
    pd.DataFrame   OHLCV with DatetimeIndex
    """
    yf_symbol = resolve_symbol(symbol)

    df = pd.DataFrame()

    # --- Attempt Yahoo Finance download ---
    if _YF_AVAILABLE:
        logger.info(
            "Downloading %s  period=%s  interval=%s", yf_symbol, period, interval
        )
        try:
            df = yf.download(
                yf_symbol, period=period, interval=interval, progress=False
            )
        except Exception as exc:
            logger.warning("yfinance download failed for %s: %s", yf_symbol, exc)
            df = pd.DataFrame()

    if not df.empty:
        # Flatten MultiIndex columns if present
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df.index.name = "Date"
        if save:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            path = DATA_DIR / f"{yf_symbol.replace('.', '_')}_{interval}.csv"
            df.to_csv(path)
            logger.info("Saved %d rows → %s", len(df), path)
        return df

    # --- Fallback: generate synthetic data ---
    logger.warning(
        "Yahoo Finance unavailable for %s — generating synthetic data "
        "(demo fallback). Data is simulated and NOT real market prices.",
        yf_symbol,
    )
    return generate_synthetic_ohlcv(yf_symbol, days=500, save=save)


def load_cached_ohlcv(symbol: str, interval: str = "1d") -> pd.DataFrame:
    """Load previously downloaded CSV.  Returns empty DataFrame on miss."""
    yf_symbol = resolve_symbol(symbol)
    path = DATA_DIR / f"{yf_symbol.replace('.', '_')}_{interval}.csv"
    if path.exists():
        df = pd.read_csv(path, index_col="Date", parse_dates=True)
        return df
    return pd.DataFrame()


# ---------------------------------------------------------------------------
# Tick simulator
# ---------------------------------------------------------------------------


def tick_generator(
    symbol: str,
    interval_sec: float = TICK_INTERVAL_SEC,
    loop: bool = True,
) -> Generator[dict, None, None]:
    """
    Yield simulated ticks by replaying daily close prices.

    Each tick is a dict::

        {"symbol": "RELIANCE.NS", "price": 2543.10, "volume": 123456,
         "timestamp": "2026-02-12T10:30:00"}

    The generator sleeps ``interval_sec`` between ticks.
    When ``loop=True`` (default) it wraps around to the beginning of the dataset.
    """
    df = load_cached_ohlcv(symbol)
    if df.empty:
        logger.info("No cached data for %s; fetching (with fallback) …", symbol)
        df = download_ohlcv(symbol)
    if df.empty:
        logger.error("Cannot generate ticks — no data for %s", symbol)
        return

    yf_symbol = resolve_symbol(symbol)
    idx = 0
    while True:
        row = df.iloc[idx]
        tick = {
            "symbol": yf_symbol,
            "open": float(row.get("Open", row["Close"])),
            "high": float(row.get("High", row["Close"])),
            "low": float(row.get("Low", row["Close"])),
            "close": float(row["Close"]),
            "price": float(row["Close"]),
            "volume": int(row.get("Volume", 0)),
            "timestamp": datetime.utcnow().isoformat(),
        }
        yield tick
        time.sleep(interval_sec)
        idx += 1
        if idx >= len(df):
            if loop:
                idx = 0
            else:
                return


def fetch_default_symbols() -> None:
    """Download OHLCV for all default symbols (with automatic synthetic fallback)."""
    for sym in DEFAULT_SYMBOLS:
        download_ohlcv(sym, period="2y", interval="1d", save=True)
