"""
app/broker/order_manager.py
===========================
Order lifecycle management with retry logic and idempotency.

Order states::

    NEW → ACK → PARTIAL → FILLED
                       ↘ CANCELLED
                       ↘ REJECTED

The order manager is the single authority on order state transitions.
"""

import logging
import threading
import uuid
from datetime import datetime
from typing import Any, Callable, Optional

from app.db import storage
from app.utils.risk import RiskParams, position_size, stop_loss_price, take_profit_price
from app.config import INITIAL_CAPITAL

logger = logging.getLogger(__name__)

# Valid state transitions
_VALID_TRANSITIONS: dict[str, set[str]] = {
    "NEW": {"ACK", "REJECTED", "CANCELLED"},
    "ACK": {"PARTIAL", "FILLED", "CANCELLED", "REJECTED"},
    "PARTIAL": {"PARTIAL", "FILLED", "CANCELLED"},
}

# Max retries for broker submission
MAX_RETRIES = 3


class OrderManager:
    """
    Manages all order operations: creation, state tracking, retries,
    idempotency, and position bookkeeping.
    """

    def __init__(self, broker_submit_fn: Optional[Callable] = None):
        """
        Parameters
        ----------
        broker_submit_fn : callable(order_dict) -> bool
            Function that submits an order to the (simulated) broker.
            Should return True on successful submission.
        """
        self._broker_submit = broker_submit_fn
        self._lock = threading.Lock()
        # In-memory position: symbol -> {"qty": int, "avg_price": float, "side": str}
        self._positions: dict[str, dict[str, Any]] = {}
        # Order cache: order_id -> order dict
        self._orders: dict[str, dict] = {}
        # Idempotency set: signal hashes recently processed
        self._recent_signals: set[str] = set()
        # Capital tracking
        self.capital: float = INITIAL_CAPITAL
        self.realised_pnl: float = 0.0

    # ------------------------------------------------------------------
    # Order creation from strategy signal
    # ------------------------------------------------------------------

    def handle_signal(self, signal: dict) -> Optional[dict]:
        """
        Convert a strategy signal into an order and submit it.

        Implements idempotency: duplicate signals within the same tick are
        ignored (keyed on symbol + action + price).
        """
        sig_key = f"{signal['symbol']}_{signal['action']}_{signal['price']}"
        if sig_key in self._recent_signals:
            logger.debug("Duplicate signal ignored: %s", sig_key)
            return None
        self._recent_signals.add(sig_key)
        # Cap size of recent set
        if len(self._recent_signals) > 500:
            self._recent_signals.clear()

        # Calculate position size via risk engine
        params = RiskParams(capital=self.capital)
        qty = position_size(signal["price"], params)

        order = {
            "order_id": str(uuid.uuid4()),
            "symbol": signal["symbol"],
            "side": signal["action"],
            "qty": qty,
            "price": signal["price"],
            "order_type": "MARKET",
            "status": "NEW",
            "filled_qty": 0,
            "avg_price": 0.0,
            "strategy": signal.get("strategy", "manual"),
            "stop_loss": stop_loss_price(signal["price"], signal["action"]),
            "take_profit": take_profit_price(signal["price"], signal["action"]),
            "created_at": datetime.utcnow().isoformat(),
            "updated_at": datetime.utcnow().isoformat(),
            "retries": 0,
        }

        with self._lock:
            self._orders[order["order_id"]] = order

        storage.insert_order(order)
        logger.info(
            "Order created: %s %s %s qty=%d @ %.2f",
            order["order_id"][:8],
            order["side"],
            order["symbol"],
            qty,
            order["price"],
        )

        self._submit_with_retry(order)
        return order

    def place_manual_order(
        self, symbol: str, side: str, qty: int, price: float
    ) -> dict:
        """Place a manual (non-strategy) order."""
        order = {
            "order_id": str(uuid.uuid4()),
            "symbol": symbol,
            "side": side.upper(),
            "qty": qty,
            "price": price,
            "order_type": "MARKET",
            "status": "NEW",
            "filled_qty": 0,
            "avg_price": 0.0,
            "strategy": "manual",
            "stop_loss": stop_loss_price(price, side),
            "take_profit": take_profit_price(price, side),
            "created_at": datetime.utcnow().isoformat(),
            "updated_at": datetime.utcnow().isoformat(),
            "retries": 0,
        }
        with self._lock:
            self._orders[order["order_id"]] = order
        storage.insert_order(order)
        self._submit_with_retry(order)
        return order

    # ------------------------------------------------------------------
    # Submission with retry
    # ------------------------------------------------------------------

    def _submit_with_retry(self, order: dict) -> None:
        """Submit to broker, retrying up to MAX_RETRIES times."""
        if self._broker_submit is None:
            logger.warning("No broker_submit_fn configured — order stays NEW")
            return

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                ok = self._broker_submit(order)
                if ok:
                    logger.debug(
                        "Order %s submitted (attempt %d)",
                        order["order_id"][:8],
                        attempt,
                    )
                    return
            except Exception as exc:
                logger.warning("Broker submit attempt %d failed: %s", attempt, exc)
            order["retries"] = attempt

        logger.error(
            "Order %s failed after %d retries — marking REJECTED",
            order["order_id"][:8],
            MAX_RETRIES,
        )
        self.update_order_status(order["order_id"], "REJECTED")

    # ------------------------------------------------------------------
    # State transitions (called by webhook handler)
    # ------------------------------------------------------------------

    def update_order_status(
        self,
        order_id: str,
        new_status: str,
        filled_qty: int = 0,
        avg_price: float = 0.0,
    ) -> Optional[dict]:
        """
        Transition an order to a new status.  Validates allowed transitions.
        Returns the updated order dict or None if transition is invalid.
        """
        with self._lock:
            order = self._orders.get(order_id)
            if order is None:
                order_row = storage.get_order(order_id)
                if order_row:
                    order = order_row
                    self._orders[order_id] = order
                else:
                    logger.error("Order %s not found", order_id)
                    return None

            current = order["status"]
            allowed = _VALID_TRANSITIONS.get(current, set())
            if new_status not in allowed:
                logger.warning(
                    "Invalid transition %s → %s for order %s",
                    current,
                    new_status,
                    order_id[:8],
                )
                return None

            order["status"] = new_status
            order["updated_at"] = datetime.utcnow().isoformat()
            if filled_qty > 0:
                order["filled_qty"] = filled_qty
            if avg_price > 0:
                order["avg_price"] = avg_price

        # Persist
        storage.update_order(
            order_id,
            {
                "status": new_status,
                "filled_qty": order["filled_qty"],
                "avg_price": order["avg_price"],
            },
        )

        # Update positions on fill
        if new_status in ("FILLED", "PARTIAL"):
            self._update_position(order)

        logger.info(
            "Order %s: %s → %s  filled=%d  avg=%.2f",
            order_id[:8],
            current,
            new_status,
            order["filled_qty"],
            order["avg_price"],
        )
        return order

    # ------------------------------------------------------------------
    # Position tracking
    # ------------------------------------------------------------------

    def _update_position(self, order: dict) -> None:
        """Update in-memory position after a fill."""
        sym = order["symbol"]
        side = order["side"]
        fill_qty = order["filled_qty"]
        fill_price = order["avg_price"]

        with self._lock:
            pos = self._positions.get(sym, {"qty": 0, "avg_price": 0.0, "side": "FLAT"})

            if pos["side"] == "FLAT" or pos["qty"] == 0:
                pos = {"qty": fill_qty, "avg_price": fill_price, "side": side}
            elif pos["side"] == side:
                # Adding to position
                total_qty = pos["qty"] + fill_qty
                pos["avg_price"] = (
                    (pos["avg_price"] * pos["qty"]) + (fill_price * fill_qty)
                ) / total_qty
                pos["qty"] = total_qty
            else:
                # Reducing / closing position
                if fill_qty >= pos["qty"]:
                    # Close PnL
                    pnl = (fill_price - pos["avg_price"]) * pos["qty"]
                    if pos["side"] == "SELL":
                        pnl = -pnl
                    self.realised_pnl += pnl
                    remaining = fill_qty - pos["qty"]
                    if remaining > 0:
                        pos = {"qty": remaining, "avg_price": fill_price, "side": side}
                    else:
                        pos = {"qty": 0, "avg_price": 0.0, "side": "FLAT"}
                else:
                    pnl = (fill_price - pos["avg_price"]) * fill_qty
                    if pos["side"] == "SELL":
                        pnl = -pnl
                    self.realised_pnl += pnl
                    pos["qty"] -= fill_qty

            self._positions[sym] = pos

        storage.insert_trade(
            {
                "order_id": order["order_id"],
                "symbol": sym,
                "side": side,
                "qty": fill_qty,
                "price": fill_price,
            }
        )

    # ------------------------------------------------------------------
    # Cancel
    # ------------------------------------------------------------------

    def cancel_order(self, order_id: str) -> bool:
        """Request cancellation of an open order."""
        with self._lock:
            order = self._orders.get(order_id)
        if order is None:
            return False
        if order["status"] in ("NEW", "ACK", "PARTIAL"):
            self.update_order_status(order_id, "CANCELLED")
            return True
        return False

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get_positions(self) -> dict:
        with self._lock:
            return dict(self._positions)

    def get_open_orders(self) -> list[dict]:
        with self._lock:
            return [
                o
                for o in self._orders.values()
                if o["status"] in ("NEW", "ACK", "PARTIAL")
            ]

    def get_all_orders(self) -> list[dict]:
        with self._lock:
            return list(self._orders.values())

    def get_pnl(self, current_prices: Optional[dict[str, float]] = None) -> dict:
        """
        Compute realised + unrealised PnL.

        Parameters
        ----------
        current_prices : dict mapping symbol → latest price (for unrealised)
        """
        unrealised = 0.0
        if current_prices:
            with self._lock:
                for sym, pos in self._positions.items():
                    if pos["qty"] > 0 and sym in current_prices:
                        diff = current_prices[sym] - pos["avg_price"]
                        if pos["side"] == "SELL":
                            diff = -diff
                        unrealised += diff * pos["qty"]
        return {
            "realised_pnl": round(self.realised_pnl, 2),
            "unrealised_pnl": round(unrealised, 2),
            "total_pnl": round(self.realised_pnl + unrealised, 2),
            "capital": round(self.capital + self.realised_pnl + unrealised, 2),
        }
