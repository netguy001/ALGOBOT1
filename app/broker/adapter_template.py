"""
app/broker/adapter_template.py
==============================
Skeleton adapter interface for wiring real brokers (e.g. Zerodha Kite).

This file is intentionally *not* functional — it serves as a template showing
how to plug a production broker into the system by implementing the abstract
methods.
"""

from abc import ABC, abstractmethod
from typing import Any


class BrokerAdapter(ABC):
    """
    Abstract broker adapter.

    To integrate a real broker:
    1. Subclass ``BrokerAdapter``.
    2. Implement all abstract methods.
    3. Pass an instance to ``OrderManager(broker_submit_fn=adapter.place_order)``.
    """

    @abstractmethod
    def connect(self, credentials: dict[str, str]) -> bool:
        """Establish a session with the broker."""
        ...

    @abstractmethod
    def place_order(self, order: dict[str, Any]) -> str:
        """
        Submit an order.  Returns broker-assigned order ID.

        ``order`` contains at minimum:
            symbol, side, qty, price, order_type
        """
        ...

    @abstractmethod
    def cancel_order(self, broker_order_id: str) -> bool:
        """Cancel an order by its broker-assigned ID."""
        ...

    @abstractmethod
    def get_order_status(self, broker_order_id: str) -> dict:
        """Return the current status of an order."""
        ...

    @abstractmethod
    def get_positions(self) -> list[dict]:
        """Return all open positions from the broker."""
        ...

    @abstractmethod
    def disconnect(self) -> None:
        """Close the broker session."""
        ...


# ---------------------------------------------------------------------------
# Example: Zerodha Kite skeleton (non-functional)
# ---------------------------------------------------------------------------


class ZerodhaAdapter(BrokerAdapter):
    """
    Example adapter for Zerodha Kite Connect.

    Prerequisites (not included in this demo):
        pip install kiteconnect
        Obtain api_key and access_token from https://kite.trade

    This class is a *template* — replace the ``raise NotImplementedError``
    lines with real Kite API calls.
    """

    def __init__(self):
        self._kite = None

    def connect(self, credentials: dict[str, str]) -> bool:
        # from kiteconnect import KiteConnect
        # self._kite = KiteConnect(api_key=credentials["api_key"])
        # self._kite.set_access_token(credentials["access_token"])
        raise NotImplementedError("Wire kiteconnect here")

    def place_order(self, order: dict[str, Any]) -> str:
        # return self._kite.place_order(
        #     variety="regular",
        #     exchange="NSE",
        #     tradingsymbol=order["symbol"].replace(".NS", ""),
        #     transaction_type=order["side"],
        #     quantity=order["qty"],
        #     product="MIS",
        #     order_type="MARKET",
        # )
        raise NotImplementedError

    def cancel_order(self, broker_order_id: str) -> bool:
        raise NotImplementedError

    def get_order_status(self, broker_order_id: str) -> dict:
        raise NotImplementedError

    def get_positions(self) -> list[dict]:
        raise NotImplementedError

    def disconnect(self) -> None:
        raise NotImplementedError
