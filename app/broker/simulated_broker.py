"""
app/broker/simulated_broker.py
==============================
A simulated broker that mimics realistic order processing with configurable
latencies, partial fills, slippage, and webhook callbacks.

This module runs its own background thread to process an order queue.

Implements the BrokerAdapter ABC for consistency with production broker integrations.
"""

import logging
import random
import threading
import time
from typing import Any, Callable, Optional

import requests

from app.broker.adapter_template import BrokerAdapter
from app.config import (
    BROKER_MAX_LATENCY_MS,
    BROKER_MIN_LATENCY_MS,
    FLASK_PORT,
    SLIPPAGE_PCT,
)

logger = logging.getLogger(__name__)


class SimulatedBroker(BrokerAdapter):
    """
    Simulated broker implementing BrokerAdapter ABC.

    Accepts orders, simulates realistic exchange behaviour, and fires
    webhook callbacks at ``/webhook/order-update``.

    Lifecycle per order::

        receive → ACK (200-400 ms) → PARTIAL (optional) → FILLED/REJECTED

    Partial fills happen with ~30 % probability for orders > 10 shares.
    """

    def __init__(
        self,
        webhook_url: Optional[str] = None,
        on_update: Optional[Callable] = None,
    ):
        self._webhook_url = (
            webhook_url or f"http://127.0.0.1:{FLASK_PORT}/webhook/order-update"
        )
        self._queue: list[dict] = []
        self._lock = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._on_update = on_update  # optional in-process callback
        self._orders: dict[str, dict] = {}  # track orders for get_order_status
        self._connected: bool = False

    # ------------------------------------------------------------------
    # BrokerAdapter ABC implementation
    # ------------------------------------------------------------------

    def connect(self, credentials: dict[str, str] = None) -> bool:
        """
        Establish broker connection.

        For SimulatedBroker, this starts the background processing thread.
        Credentials are ignored (simulation only).
        """
        if self._connected:
            return True
        self.start()
        self._connected = True
        logger.info("SimulatedBroker connected")
        return True

    def place_order(self, order: dict[str, Any]) -> str:
        """
        Submit an order. Returns the order_id.

        This is the BrokerAdapter-compliant method.
        Internally calls submit_order for backward compatibility.
        """
        if "order_id" not in order:
            import uuid

            order["order_id"] = str(uuid.uuid4())

        # Track order for get_order_status
        with self._lock:
            self._orders[order["order_id"]] = {
                **order,
                "status": "NEW",
                "filled_qty": 0,
                "avg_price": 0.0,
            }

        self.submit_order(order)
        return order["order_id"]

    def cancel_order(self, broker_order_id: str) -> bool:
        """
        Cancel an order by ID.

        In simulation mode, removes from queue if not yet processed.
        """
        with self._lock:
            # Try to remove from pending queue
            for i, o in enumerate(self._queue):
                if o.get("order_id") == broker_order_id:
                    self._queue.pop(i)
                    if broker_order_id in self._orders:
                        self._orders[broker_order_id]["status"] = "CANCELLED"
                    logger.info(
                        "Order %s cancelled (removed from queue)", broker_order_id[:8]
                    )
                    return True
            # If already processing, cannot cancel
            if broker_order_id in self._orders:
                status = self._orders[broker_order_id].get("status", "")
                if status in ("NEW", "ACK"):
                    self._orders[broker_order_id]["status"] = "CANCELLED"
                    return True
        logger.warning(
            "Cannot cancel order %s (already processed)", broker_order_id[:8]
        )
        return False

    def get_order_status(self, broker_order_id: str) -> dict:
        """Return the current status of an order."""
        with self._lock:
            order = self._orders.get(broker_order_id)
            if order:
                return {
                    "order_id": broker_order_id,
                    "status": order.get("status", "UNKNOWN"),
                    "filled_qty": order.get("filled_qty", 0),
                    "avg_price": order.get("avg_price", 0.0),
                    "symbol": order.get("symbol", ""),
                    "side": order.get("side", ""),
                    "qty": order.get("qty", 0),
                }
        return {"order_id": broker_order_id, "status": "NOT_FOUND"}

    def get_positions(self) -> list[dict]:
        """
        Return all open positions.

        For SimulatedBroker, positions are managed by CapitalManager,
        not the broker itself. Returns empty list.
        """
        # In simulation, OrderManager/CapitalManager track positions
        return []

    def disconnect(self) -> None:
        """Close the broker session."""
        self.stop()
        self._connected = False
        logger.info("SimulatedBroker disconnected")

    # ------------------------------------------------------------------
    # Legacy Public API (backward compatible)
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the background processing thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._process_loop, daemon=True)
        self._thread.start()
        logger.info("Simulated broker started")

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("Simulated broker stopped")

    def submit_order(self, order: dict) -> bool:
        """
        Enqueue an order for processing.  Returns True immediately
        (simulating async broker acceptance).
        """
        with self._lock:
            self._queue.append(order.copy())
        logger.debug("Broker received order %s", order["order_id"][:8])
        return True

    # ------------------------------------------------------------------
    # Background processing
    # ------------------------------------------------------------------

    def _process_loop(self) -> None:
        """Drain the order queue, simulating latency for each order."""
        while self._running:
            order = None
            with self._lock:
                if self._queue:
                    order = self._queue.pop(0)
            if order is None:
                time.sleep(0.05)
                continue
            self._simulate_order(order)

    def _simulate_order(self, order: dict) -> None:
        """Simulate the full lifecycle of a single order."""
        oid = order["order_id"]

        # --- ACK ---
        latency = random.randint(BROKER_MIN_LATENCY_MS, BROKER_MAX_LATENCY_MS) / 2
        time.sleep(latency / 1000)
        self._update_order_status(oid, "ACK", 0, 0.0)
        self._fire_callback(oid, "ACK", 0, 0.0)

        # --- Resolve fill price (must be > 0) ---
        fill_price = order.get("price", 0.0)
        if fill_price <= 0:
            # MARKET order with no explicit price — use last known price
            fill_price = order.get("market_price", 0.0)
        if fill_price <= 0:
            logger.warning(
                "Order %s has price=0 — REJECTING (no market price available)",
                oid[:8],
            )
            self._update_order_status(oid, "REJECTED", 0, 0.0)
            self._fire_callback(oid, "REJECTED", 0, 0.0)
            return

        # --- Apply slippage ---
        if fill_price > 0:
            slip = fill_price * (SLIPPAGE_PCT / 100)
            if order["side"] == "BUY":
                fill_price += slip
            else:
                fill_price -= slip
            fill_price = round(fill_price, 2)

        total_qty = order["qty"]

        # --- Decide on partial fills (30 % chance when qty > 10) ---
        do_partial = total_qty > 10 and random.random() < 0.3

        if do_partial:
            partial_qty = random.randint(1, total_qty - 1)
            # Send PARTIAL
            time.sleep(
                random.randint(BROKER_MIN_LATENCY_MS, BROKER_MAX_LATENCY_MS) / 1000
            )
            self._update_order_status(oid, "PARTIAL", partial_qty, fill_price)
            self._fire_callback(oid, "PARTIAL", partial_qty, fill_price)

            # Remaining fill
            time.sleep(
                random.randint(BROKER_MIN_LATENCY_MS, BROKER_MAX_LATENCY_MS) / 1000
            )
            self._update_order_status(oid, "FILLED", total_qty, fill_price)
            self._fire_callback(oid, "FILLED", total_qty, fill_price)
        else:
            # Direct fill
            time.sleep(
                random.randint(BROKER_MIN_LATENCY_MS, BROKER_MAX_LATENCY_MS) / 1000
            )

            # Small chance (~5 %) of rejection for realism
            if random.random() < 0.05:
                self._update_order_status(oid, "REJECTED", 0, 0.0)
                self._fire_callback(oid, "REJECTED", 0, 0.0)
            else:
                self._update_order_status(oid, "FILLED", total_qty, fill_price)
                self._fire_callback(oid, "FILLED", total_qty, fill_price)

    def _update_order_status(
        self, order_id: str, status: str, filled_qty: int, avg_price: float
    ) -> None:
        """Update internal order tracking."""
        with self._lock:
            if order_id in self._orders:
                self._orders[order_id]["status"] = status
                self._orders[order_id]["filled_qty"] = filled_qty
                self._orders[order_id]["avg_price"] = avg_price

    def _fire_callback(
        self, order_id: str, status: str, filled_qty: int, avg_price: float
    ) -> None:
        """Send the update via in-process callback and/or HTTP webhook."""
        payload: dict[str, Any] = {
            "order_id": order_id,
            "status": status,
            "filled_qty": filled_qty,
            "avg_price": avg_price,
        }

        # In-process callback (faster, avoids HTTP overhead)
        if self._on_update:
            try:
                self._on_update(payload)
                logger.debug(
                    "Broker callback: %s → %s  filled=%d",
                    order_id[:8],
                    status,
                    filled_qty,
                )
                return  # skip HTTP webhook — callback handled it
            except Exception as exc:
                logger.error("In-process broker callback error: %s", exc)

        # HTTP webhook fallback (only when no in-process callback)
        try:
            requests.post(self._webhook_url, json=payload, timeout=2)
        except Exception:
            # Webhook delivery is best-effort in a demo
            pass

        logger.debug(
            "Broker callback: %s → %s  filled=%d", order_id[:8], status, filled_qty
        )
