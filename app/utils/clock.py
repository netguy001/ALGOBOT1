"""
app/utils/clock.py
==================
EngineClock — **single authoritative time source** for the entire engine.

EVERY timestamp in the system must originate from this module:

    EngineClock.now_utc()   → timezone-aware UTC datetime
    EngineClock.now_iso()   → ISO 8601 UTC string (``2026-02-13T14:25:30.123456+00:00``)
    EngineClock.now()       → IST datetime (display only)
    EngineClock.epoch()     → Unix epoch (int, UTC)

Additional responsibilities:
    - Market-session awareness (NSE 09:15–15:30 IST)
    - Candle-boundary alignment for any timeframe
    - MODE support (demo always-open vs paper/live calendar)

NSE trading hours:
    Pre-open  : 09:00–09:15 IST
    Normal    : 09:15–15:30 IST
    Closing   : 15:30–15:40 IST (auction, not relevant for demo)

In demo mode, ``is_market_open()`` always returns True so the tick
loop and strategies run 24/7.
"""

import json
import logging
import math
from datetime import datetime, time, timezone, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# IST = UTC+5:30
IST = timezone(timedelta(hours=5, minutes=30))

# NSE normal trading session
_MARKET_OPEN = time(9, 15)
_MARKET_CLOSE = time(15, 30)

# Holiday list — loaded from JSON if available
_HOLIDAYS: set[str] = set()  # format: "YYYY-MM-DD"

# Valid candle timeframes → duration in seconds
TIMEFRAME_SECONDS: dict[str, int] = {
    "1m": 60,
    "3m": 180,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "4h": 14400,
    "1d": 86400,
}


def _load_holidays() -> set[str]:
    """Load NSE holidays from data/nse_holidays.json if it exists."""
    global _HOLIDAYS
    p = Path(__file__).resolve().parent.parent.parent / "data" / "nse_holidays.json"
    if p.exists():
        try:
            with open(p) as f:
                data = json.load(f)
            _HOLIDAYS = set(data) if isinstance(data, list) else set()
            logger.info("Loaded %d NSE holidays from %s", len(_HOLIDAYS), p)
        except Exception as exc:
            logger.warning("Failed to load holiday file: %s", exc)
    return _HOLIDAYS


_load_holidays()


class EngineClock:
    """Centralised clock for the trading engine.

    **ALL modules must use this clock** instead of calling
    ``datetime.now()``, ``datetime.utcnow()``, or ``time.time()``
    directly.

    Usage::

        clock = EngineClock(mode="demo")
        clock.now_utc()          # → datetime in UTC (timezone-aware)
        clock.now_iso()          # → "2026-02-13T14:25:30.123456+00:00"
        clock.epoch()            # → 1739454330  (int)
        clock.now()              # → datetime in IST (display only)
        clock.is_market_open()   # → True in demo mode always

    Parameters
    ----------
    mode : str
        "demo" — market hours check always returns True
        "paper" | "live" — respects NSE calendar
    """

    def __init__(self, mode: str = "demo"):
        self._mode = mode.lower()

    @property
    def mode(self) -> str:
        return self._mode

    # ── Time queries ────────────────────────────────────────

    def now_utc(self) -> datetime:
        """Return current time in UTC (timezone-aware).

        This is the PRIMARY time function. All timestamps stored in DB,
        attached to orders/trades/ticks, and broadcast via WebSocket
        must originate from this method.
        """
        return datetime.now(timezone.utc)

    def now_iso(self) -> str:
        """Return current UTC time as ISO 8601 string.

        Format: ``2026-02-13T14:25:30.123456+00:00``

        Use this for all string timestamps (DB writes, JSON payloads).
        """
        return self.now_utc().isoformat()

    def epoch(self) -> int:
        """Return current UTC time as Unix epoch (seconds).

        Use this for candle timestamps in the DB (integer column).
        """
        return int(self.now_utc().timestamp())

    def now(self) -> datetime:
        """Return current time in IST (for display purposes only).

        WARNING: Do NOT store this value in the DB.  Use ``now_utc()``
        or ``now_iso()`` for persistence.
        """
        return datetime.now(IST)

    def today_str(self) -> str:
        """Return today's date as 'YYYY-MM-DD' in IST."""
        return self.now().strftime("%Y-%m-%d")

    # ── Candle boundary helpers ────────────────────────────

    def candle_boundary(
        self, timeframe: str = "1m", epoch_time: Optional[int] = None
    ) -> int:
        """Return the candle open-time (epoch) for the given timeframe.

        Aligns to exact timeframe boundaries::

            5m → 20:00, 20:05, 20:10 …
            1m → 20:00, 20:01, 20:02 …
            1h → 20:00, 21:00, 22:00 …

        Parameters
        ----------
        timeframe : str
            One of: 1m, 3m, 5m, 15m, 30m, 1h, 4h, 1d
        epoch_time : int | None
            Unix epoch to align.  Defaults to ``self.epoch()``.

        Returns
        -------
        int
            Epoch (seconds) of the candle boundary.
        """
        if epoch_time is None:
            epoch_time = self.epoch()
        secs = TIMEFRAME_SECONDS.get(timeframe, 60)
        return (epoch_time // secs) * secs

    # ── Market session ──────────────────────────────────────

    def is_market_open(self) -> bool:
        """Check if NSE market is currently open.

        In demo mode this always returns True.
        In paper/live mode it checks:
            1. Weekday (Mon-Fri)
            2. Not a holiday
            3. Time between 09:15 and 15:30 IST
        """
        if self._mode == "demo":
            return True

        now_ist = self.now()

        # Weekend check
        if now_ist.weekday() >= 5:  # 5=Sat, 6=Sun
            return False

        # Holiday check
        if now_ist.strftime("%Y-%m-%d") in _HOLIDAYS:
            return False

        # Time check
        current_time = now_ist.time()
        return _MARKET_OPEN <= current_time <= _MARKET_CLOSE

    def is_pre_open(self) -> bool:
        """Check if we're in the NSE pre-open session (09:00-09:15 IST)."""
        if self._mode == "demo":
            return False
        now_ist = self.now()
        if now_ist.weekday() >= 5:
            return False
        current_time = now_ist.time()
        return time(9, 0) <= current_time < time(9, 15)

    def seconds_to_open(self) -> Optional[int]:
        """Seconds until market opens. None if already open or demo mode."""
        if self._mode == "demo":
            return None
        if self.is_market_open():
            return 0
        now_ist = self.now()
        open_today = now_ist.replace(
            hour=_MARKET_OPEN.hour, minute=_MARKET_OPEN.minute, second=0, microsecond=0
        )
        if now_ist.time() > _MARKET_CLOSE:
            # Market closed for today — next open is tomorrow (skip weekends)
            days_ahead = 1
            while True:
                next_day = now_ist + timedelta(days=days_ahead)
                if (
                    next_day.weekday() < 5
                    and next_day.strftime("%Y-%m-%d") not in _HOLIDAYS
                ):
                    break
                days_ahead += 1
            open_today = next_day.replace(
                hour=_MARKET_OPEN.hour,
                minute=_MARKET_OPEN.minute,
                second=0,
                microsecond=0,
            )
        diff = (open_today - now_ist).total_seconds()
        return max(0, int(diff))

    def to_dict(self) -> dict:
        """Serialise clock state for the API / UI."""
        now = self.now()
        return {
            "utc_timestamp": self.now_iso(),
            "ist_time": now.strftime("%H:%M:%S"),
            "ist_date": now.strftime("%Y-%m-%d"),
            "market_open": self.is_market_open(),
            "pre_open": self.is_pre_open(),
            "mode": self._mode,
            "seconds_to_open": self.seconds_to_open(),
        }
