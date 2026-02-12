"""
app/data_feed/base.py
=====================
Abstract data-feed interface.

All data sources (demo replay, Kite, Alpaca, etc.) implement this contract.
The rest of the system depends ONLY on this interface — never on Yahoo
Finance or any specific provider directly.

Usage::

    feed = DemoDataFeed(symbols=["RELIANCE.NS", "TCS.NS"])
    feed.on_tick(my_callback)
    feed.connect()
    # ... ticks flow via callback ...
    feed.disconnect()
"""

from abc import ABC, abstractmethod
from typing import Callable, Optional


# Type alias for the tick callback signature
TickCallback = Callable[[dict], None]


class DataFeed(ABC):
    """
    Abstract base class for market data feeds.

    Subclasses must implement all abstract methods. The lifecycle is:
        1. Instantiate with symbol list
        2. Register callback(s) via ``on_tick``
        3. Call ``connect()`` to start data flow
        4. Call ``disconnect()`` to stop
    """

    @abstractmethod
    def connect(self) -> None:
        """
        Establish connection to the data source and begin streaming.

        For demo feeds this starts the replay loop.
        For live feeds this authenticates and opens websockets.
        """
        ...

    @abstractmethod
    def disconnect(self) -> None:
        """Stop data flow and release resources."""
        ...

    @abstractmethod
    def subscribe(self, symbol: str) -> None:
        """
        Add a symbol to the subscription list.

        Can be called before or after ``connect()``.
        """
        ...

    @abstractmethod
    def unsubscribe(self, symbol: str) -> None:
        """Remove a symbol from the subscription list."""
        ...

    @abstractmethod
    def on_tick(self, callback: TickCallback) -> None:
        """
        Register a callback that will be invoked for every tick.

        Callback signature::

            def handle(tick: dict) -> None:
                # tick = {"symbol": str, "price": float, "open": float,
                #         "high": float, "low": float, "close": float,
                #         "volume": int, "timestamp": str}
        """
        ...

    @abstractmethod
    def get_symbols(self) -> list[str]:
        """Return the list of currently subscribed symbols."""
        ...

    @property
    @abstractmethod
    def is_connected(self) -> bool:
        """Whether the feed is actively streaming."""
        ...
