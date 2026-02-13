"""
app/broker/order_manager.py
===========================
Order lifecycle management with retry logic and idempotency.

Order states::

    NEW â†’ ACK â†’ PARTIAL â†’ FILLED
                       â†˜ CANCELLED
                       â†˜ REJECTED

The order manager is the single authority on order state transitions.
Position and capital tracking is *delegated* to ``CapitalManager``.
Pre-trade validation is delegated to ``OrderValidator``.
"""

import logging
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from app.db import storage
from app.utils.risk import RiskParams, position_size, stop_loss_price, take_profit_price
from app.config import ORDER_TIMEOUT_SEC


def _utc_now_iso() -> str:
    """Return current UTC time as ISO 8601 string via EngineClock."""
    try:
        from app.utils.clock import EngineClock

        return EngineClock(mode="demo").now_iso()
    except Exception:
        return datetime.now(timezone.utc).isoformat()


def _utc_now_dt() -> datetime:
    """Return current UTC datetime (timezone-aware) via EngineClock."""
    try:
        from app.utils.clock import EngineClock

        return EngineClock(mode="demo").now_utc()
    except Exception:
        return datetime.now(timezone.utc)


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
    idempotency, and position bookkeeping (via CapitalManager).
    """

    def __init__(
        self,
        broker_submit_fn: Optional[Callable] = None,
        capital_mgr=None,
        order_validator=None,
    ):
        """
        Parameters
        ----------
        broker_submit_fn : callable(order_dict) -> bool
            Function that submits an order to the (simulated) broker.
        capital_mgr : CapitalManager | None
            Centralised capital/position tracker. If None, a minimal
            fallback is used (for backward-compat in tests).
        order_validator : OrderValidator | None
            Pre-trade validation layer. If None, validation is skipped.
        """
        self._broker_submit = broker_submit_fn
        self._lock = threading.Lock()

        # External dependencies (injected, not owned)
        self._capital_mgr = capital_mgr
        self._order_validator = order_validator

        # Order cache: order_id -> order dict
        self._orders: dict[str, dict] = {}

        # Engine stop callback (set by main.py for daily-loss halt)
        self._engine_stop_fn: Optional[Callable] = None

        # Restore recent orders from DB so SL/TP refs survive restart
        self._restore_orders_from_db()

    def _restore_orders_from_db(self) -> None:
        """Load recent filled/partial orders from DB into in-memory cache.

        This ensures SL/TP reference orders are available after a server
        restart without requiring the orders to be re-created.
        """
        try:
            recent = storage.get_all_orders(limit=200)
            for o in recent:
                self._orders[o["order_id"]] = dict(o)
            if recent:
                logger.info(
                    "Restored %d orders from DB into in-memory cache", len(recent)
                )
        except Exception as exc:
            logger.warning("Failed to restore orders from DB: %s", exc)

    # ------------------------------------------------------------------
    # Order creation from strategy signal
    # ------------------------------------------------------------------

    def handle_signal(self, signal: dict) -> Optional[dict]:
        """
        Convert a strategy signal into an order and submit it.

        Delegates pre-trade checks to OrderValidator and position sizing
        to CapitalManager.
        """
        sym = signal["symbol"]
        price = signal["price"]

        # --- Pre-trade validation ---
        if self._order_validator is not None:
            rejection = self._order_validator.validate_signal(signal)
            if rejection:
                logger.debug(
                    "Signal rejected: %s (%s %s)", rejection, signal["action"], sym
                )
                # If daily-loss halt triggered, stop the engine
                if rejection in ("daily_loss_halted", "daily_loss_limit_breached"):
                    if self._engine_stop_fn:
                        self._engine_stop_fn()
                return None
            # Record the signal for cooldown tracking
            self._order_validator.record_signal(sym)

        # --- Position sizing ---
        if self._capital_mgr is not None:
            avail = self._capital_mgr.available_capital
            params = RiskParams(capital=max(avail, 0))
            qty = position_size(price, params)
            qty = self._capital_mgr.clamp_quantity(qty, price)
        else:
            # Fallback (no capital manager)
            params = RiskParams()
            qty = position_size(price, params)

        if qty <= 0:
            logger.debug("Position size is 0 for %s â€” signal dropped", sym)
            return None

        order = {
            "order_id": str(uuid.uuid4()),
            "symbol": sym,
            "side": signal["action"],
            "qty": qty,
            "price": price,
            "order_type": "MARKET",
            "status": "NEW",
            "filled_qty": 0,
            "avg_price": 0.0,
            "strategy": signal.get("strategy", "manual"),
            "stop_loss": stop_loss_price(price, signal["action"]),
            "take_profit": take_profit_price(price, signal["action"]),
            "created_at": _utc_now_iso(),
            "updated_at": _utc_now_iso(),
            "retries": 0,
        }

        with self._lock:
            self._orders[order["order_id"]] = order

        storage.insert_order(order)
        avail_str = f"{avail:.0f}" if self._capital_mgr else "N/A"
        logger.info(
            "Order created: %s %s %s qty=%d @ %.2f  (avail_capital=%s)",
            order["order_id"][:8],
            order["side"],
            order["symbol"],
            qty,
            order["price"],
            avail_str,
        )

        self._submit_with_retry(order)
        return order

    def place_manual_order(
        self, symbol: str, side: str, qty: int, price: float
    ) -> dict:
        """Place a manual (non-strategy) order."""
        # Validate via OrderValidator if available
        if self._order_validator is not None:
            rejection = self._order_validator.validate_manual_order(
                symbol, side, qty, price
            )
            if rejection:
                logger.info("Manual order rejected: %s", rejection)
                return {"error": rejection}

        # Clamp qty through capital manager
        if self._capital_mgr is not None:
            qty = min(qty, self._capital_mgr.clamp_quantity(qty, price))
            if qty <= 0:
                return {"error": "clamped_quantity_zero"}

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
            "created_at": _utc_now_iso(),
            "updated_at": _utc_now_iso(),
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
            logger.warning("No broker_submit_fn configured â€” order stays NEW")
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
            "Order %s failed after %d retries â€” marking REJECTED",
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
                    "Invalid transition %s â†’ %s for order %s",
                    current,
                    new_status,
                    order_id[:8],
                )
                return None

            order["status"] = new_status
            order["updated_at"] = _utc_now_iso()
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
            "Order %s: %s â†’ %s  filled=%d  avg=%.2f",
            order_id[:8],
            current,
            new_status,
            order["filled_qty"],
            order["avg_price"],
        )
        return order

    # ------------------------------------------------------------------
    # Position tracking â€” delegated to CapitalManager
    # ------------------------------------------------------------------

    def _update_position(self, order: dict) -> None:
        """Update position after a fill â€” delegates to CapitalManager.

        The realised PnL from this fill is stored in the trade record
        so we have a full per-trade P&L audit trail in the DB.
        """
        sym = order["symbol"]
        side = order["side"]
        fill_qty = order["filled_qty"]
        fill_price = order["avg_price"]

        realised_pnl = 0.0
        if self._capital_mgr is not None:
            realised_pnl = self._capital_mgr.update_position(
                sym, side, fill_qty, fill_price
            )
        else:
            logger.warning("No CapitalManager â€” position tracking skipped")

        # Transactional: update order + insert trade (with PnL) in one DB commit
        trade_data = {
            "order_id": order["order_id"],
            "account_id": getattr(self._capital_mgr, "account_id", "default"),
            "symbol": sym,
            "side": side,
            "qty": fill_qty,
            "price": fill_price,
            "pnl": round(realised_pnl, 2),
        }
        try:
            storage.insert_order_and_trade(order, trade_data)
        except Exception:
            # Fallback to separate insert if transactional fails
            storage.insert_trade(trade_data)

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
        if self._capital_mgr is not None:
            return self._capital_mgr.get_positions()
        return {}

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
        Compute realised + unrealised PnL â€” delegates to CapitalManager.
        """
        if self._capital_mgr is not None:
            return self._capital_mgr.get_pnl(current_prices)

        # Fallback (no capital manager)
        return {
            "realised_pnl": 0.0,
            "unrealised_pnl": 0.0,
            "total_pnl": 0.0,
            "capital": 0.0,
            "daily_loss_halted": False,
        }

    # ------------------------------------------------------------------
    # Live SL/TP enforcement (called from tick loop)
    # ------------------------------------------------------------------

    def check_sl_tp(self, current_prices: dict[str, float]) -> list[dict]:
        """
        Check all open positions against their SL/TP prices.
        Returns list of closing orders created.
        """
        closing_orders = []

        # Get positions from CapitalManager
        if self._capital_mgr is not None:
            positions_snapshot = {
                sym: pos
                for sym, pos in self._capital_mgr.get_positions().items()
                if pos.get("qty", 0) > 0
            }
        else:
            positions_snapshot = {}

        if not positions_snapshot:
            return closing_orders

        # Gather SL/TP from the most recent filled order for each symbol
        with self._lock:
            sl_tp_map: dict[str, dict] = {}
            for o in self._orders.values():
                if (
                    o["status"] in ("FILLED", "PARTIAL")
                    and o["symbol"] in positions_snapshot
                    and o.get("stop_loss")
                    and o.get("take_profit")
                ):
                    existing = sl_tp_map.get(o["symbol"])
                    if existing is None or o["updated_at"] > existing["updated_at"]:
                        sl_tp_map[o["symbol"]] = o

        for sym, pos in positions_snapshot.items():
            price = current_prices.get(sym)
            if price is None:
                continue
            ref_order = sl_tp_map.get(sym)
            if ref_order is None:
                continue

            sl = ref_order["stop_loss"]
            tp = ref_order["take_profit"]
            side = pos["side"]
            hit = None

            if side == "BUY":
                if price <= sl:
                    hit = "SL"
                elif price >= tp:
                    hit = "TP"
            elif side == "SELL":
                if price >= sl:
                    hit = "SL"
                elif price <= tp:
                    hit = "TP"

            if hit:
                close_side = "SELL" if side == "BUY" else "BUY"
                close_order = {
                    "order_id": str(uuid.uuid4()),
                    "symbol": sym,
                    "side": close_side,
                    "qty": pos["qty"],
                    "price": price,
                    "order_type": "MARKET",
                    "status": "NEW",
                    "filled_qty": 0,
                    "avg_price": 0.0,
                    "strategy": f"auto_{hit.lower()}_exit",
                    "stop_loss": 0,
                    "take_profit": 0,
                    "created_at": _utc_now_iso(),
                    "updated_at": _utc_now_iso(),
                    "retries": 0,
                }
                with self._lock:
                    self._orders[close_order["order_id"]] = close_order
                storage.insert_order(close_order)
                self._submit_with_retry(close_order)
                closing_orders.append(close_order)
                logger.info(
                    "%s HIT for %s @ %.2f (SL=%.2f TP=%.2f) â€” closing %d shares",
                    hit,
                    sym,
                    price,
                    sl,
                    tp,
                    pos["qty"],
                )

        return closing_orders

    # ------------------------------------------------------------------
    # Order timeout cleanup
    # ------------------------------------------------------------------

    def cleanup_stale_orders(self) -> int:
        """
        Mark orders stuck in NEW status for longer than ORDER_TIMEOUT_SEC
        as REJECTED.  Returns count of timed-out orders.
        """
        now = _utc_now_dt()
        cutoff = now - timedelta(seconds=ORDER_TIMEOUT_SEC)
        timed_out = 0
        with self._lock:
            stale = [
                o
                for o in self._orders.values()
                if o["status"] == "NEW" and o.get("created_at", "") < cutoff.isoformat()
            ]
        for o in stale:
            self.update_order_status(o["order_id"], "REJECTED")
            logger.warning(
                "Order %s timed out after %ds â€” REJECTED",
                o["order_id"][:8],
                ORDER_TIMEOUT_SEC,
            )
            timed_out += 1
        return timed_out
