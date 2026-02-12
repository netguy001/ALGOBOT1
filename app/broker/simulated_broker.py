"""
app/broker/simulated_broker.py
==============================
A simulated broker that mimics realistic order processing with configurable
latencies, partial fills, slippage, and webhook callbacks.

This module runs its own background thread to process an order queue.
"""

import logging
import random
import threading
import time
from typing import Any, Callable, Optional

import requests

from app.config import (
    BROKER_MAX_LATENCY_MS,
    BROKER_MIN_LATENCY_MS,
    FLASK_PORT,
    SLIPPAGE_PCT,
)

logger = logging.getLogger(__name__)


class SimulatedBroker:
    """
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

    # ------------------------------------------------------------------
    # Public API
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
        self._fire_callback(oid, "ACK", 0, 0.0)

        # --- Apply slippage ---
        fill_price = order.get("price", 0.0)
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
            self._fire_callback(oid, "PARTIAL", partial_qty, fill_price)

            # Remaining fill
            time.sleep(
                random.randint(BROKER_MIN_LATENCY_MS, BROKER_MAX_LATENCY_MS) / 1000
            )
            self._fire_callback(oid, "FILLED", total_qty, fill_price)
        else:
            # Direct fill
            time.sleep(
                random.randint(BROKER_MIN_LATENCY_MS, BROKER_MAX_LATENCY_MS) / 1000
            )

            # Small chance (~5 %) of rejection for realism
            if random.random() < 0.05:
                self._fire_callback(oid, "REJECTED", 0, 0.0)
            else:
                self._fire_callback(oid, "FILLED", total_qty, fill_price)

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
            except Exception as exc:
                logger.error("In-process broker callback error: %s", exc)

        # HTTP webhook (fire-and-forget)
        try:
            requests.post(self._webhook_url, json=payload, timeout=2)
        except Exception:
            # Webhook delivery is best-effort in a demo
            pass

        logger.debug(
            "Broker callback: %s → %s  filled=%d", order_id[:8], status, filled_qty
        )
