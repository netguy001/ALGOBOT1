"""
app/routes/webhook.py
=====================
Webhook endpoints:

1. ``/webhook/order-update``  — Broker callback for order-state transitions
   (ACK → FILLED, REJECTED, etc.).  Used by the simulated broker.

2. ``/webhook/signal``  — **Incoming trade signal receiver**.
   External systems (TradingView, Telegram bots, custom scripts, Postman)
   POST a trade signal here and the system automatically creates and
   executes the order in real time.

Both endpoints broadcast live updates via SocketIO so the frontend
reflects changes instantly.
"""

import logging
from flask import Blueprint, jsonify, request

from app.config import SECRET_KEY

logger = logging.getLogger(__name__)

webhook_bp = Blueprint("webhook", __name__, url_prefix="/webhook")

# Injected at startup
_order_mgr = None
_socketio = None
_current_prices = None


def init_webhook_deps(order_mgr, socketio, current_prices_ref=None):
    """Inject order manager, SocketIO instance, and price map (called from main.py)."""
    global _order_mgr, _socketio, _current_prices
    _order_mgr = order_mgr
    _socketio = socketio
    _current_prices = current_prices_ref or {}


# ------------------------------------------------------------------
# Helper: resolve price for MARKET orders
# ------------------------------------------------------------------

def _resolve_market_price(symbol: str, price: float) -> float:
    """Return current market price when caller sends 0 (MARKET order)."""
    if price and price > 0:
        return price
    ltp = _current_prices.get(symbol, 0.0)
    if ltp > 0:
        return ltp
    logger.warning("No market price available for %s — using 0", symbol)
    return 0.0


# ------------------------------------------------------------------
# 1. Order-state webhook (broker callback)
# ------------------------------------------------------------------

@webhook_bp.route("/order-update", methods=["POST"])
def order_update():
    """
    Receive order-state webhook from the simulated broker.

    Expected payload::

        {
            "order_id": "uuid-string",
            "status": "ACK" | "PARTIAL" | "FILLED" | "REJECTED" | "CANCELLED",
            "filled_qty": 0,
            "avg_price": 0.0
        }
    """
    payload = request.get_json(silent=True)
    if not payload or "order_id" not in payload:
        return jsonify({"error": "Invalid payload"}), 400

    order_id = payload["order_id"]
    new_status = payload.get("status", "")
    filled_qty = int(payload.get("filled_qty", 0))
    avg_price = float(payload.get("avg_price", 0.0))

    logger.info(
        "Webhook received: order=%s status=%s filled=%d avg=%.2f",
        order_id[:8],
        new_status,
        filled_qty,
        avg_price,
    )

    updated = _order_mgr.update_order_status(
        order_id, new_status, filled_qty=filled_qty, avg_price=avg_price
    )

    if updated is None:
        return jsonify({"error": "Transition rejected"}), 400

    # Broadcast to all connected WebSocket clients
    if _socketio:
        _socketio.emit(
            "order_update",
            {
                "order_id": updated["order_id"],
                "symbol": updated["symbol"],
                "side": updated["side"],
                "qty": updated["qty"],
                "filled_qty": updated["filled_qty"],
                "avg_price": updated["avg_price"],
                "price": updated.get("price", 0),
                "status": updated["status"],
                "order_type": updated.get("order_type", "MARKET"),
                "strategy": updated.get("strategy", ""),
                "stop_loss": updated.get("stop_loss", 0),
                "take_profit": updated.get("take_profit", 0),
                "created_at": updated.get("created_at", ""),
                "updated_at": updated["updated_at"],
            },
        )

        # Also broadcast position + PnL updates
        positions = _order_mgr.get_positions()
        _socketio.emit("position_update", {"positions": positions})

        # Broadcast updated PnL so frontend shows real-time values
        try:
            pnl = _order_mgr.get_pnl(current_prices=_current_prices)
            _socketio.emit("pnl_update", pnl)
        except Exception:
            pass

    return jsonify({"ok": True}), 200


# ------------------------------------------------------------------
# 2. Incoming trade-signal webhook (TradingView / external systems)
# ------------------------------------------------------------------

@webhook_bp.route("/signal", methods=["POST"])
def receive_signal():
    """
    Receive an external trade signal and execute it as a real-time order.

    This is the endpoint you configure in TradingView alerts, Telegram
    bots, or any external system that needs to trigger live orders.

    Expected JSON payload::

        {
            "symbol":   "RELIANCE.NS",          (required)
            "action":   "BUY" | "SELL",          (required — also accepts "side")
            "qty":      10,                       (optional — auto-sized if omitted)
            "price":    0,                        (optional — 0 or omit for MARKET)
            "order_type": "MARKET" | "LIMIT",     (optional — default MARKET)
            "strategy": "tradingview",            (optional — label for audit)
            "secret":   "your-secret-key"         (optional — simple auth guard)
        }

    Returns the created order JSON on success (HTTP 201).

    Authentication:
        If ``secret`` is present in the payload it is checked against
        the server's SECRET_KEY.  Omit to skip (suitable for local/demo).

    Real-time behaviour:
        The order is submitted to the broker immediately.  As the broker
        processes it (ACK → FILLED), status updates are pushed to the
        frontend via SocketIO in real time — no polling required.
    """
    payload = request.get_json(silent=True)
    if not payload:
        return jsonify({"error": "JSON body required"}), 400

    # --- Optional auth ---
    secret = payload.get("secret")
    if secret and secret != SECRET_KEY:
        logger.warning("Webhook signal rejected: bad secret")
        return jsonify({"error": "Unauthorized"}), 401

    # --- Parse fields ---
    symbol = payload.get("symbol", "").strip()
    action = (payload.get("action") or payload.get("side") or "").strip().upper()
    strategy = payload.get("strategy", "webhook")

    if not symbol:
        return jsonify({"error": "symbol is required"}), 400
    if action not in ("BUY", "SELL"):
        return jsonify({"error": "action must be BUY or SELL"}), 400

    # Resolve price — use current market price for MARKET orders
    raw_price = float(payload.get("price", 0))
    price = _resolve_market_price(symbol, raw_price)

    if price <= 0:
        return jsonify({
            "error": f"Cannot resolve price for {symbol} — "
                     f"no market data available. Send an explicit price."
        }), 400

    # Resolve quantity — use provided qty or let the order manager auto-size
    qty = int(payload.get("qty", 0))
    if qty <= 0:
        # Auto-size: create as a strategy signal so OrderManager uses
        # CapitalManager position-sizing logic.
        signal = {
            "symbol": symbol,
            "action": action,
            "price": price,
            "strategy": strategy,
        }
        order = _order_mgr.handle_signal(signal)
        if order is None:
            return jsonify({
                "error": "Signal rejected by risk checks or position sizing"
            }), 400
    else:
        # Explicit qty: place as a manual order
        order = _order_mgr.place_manual_order(
            symbol=symbol, side=action, qty=qty, price=price
        )
        if "error" in order:
            return jsonify({"error": order["error"]}), 400

    logger.info(
        "Webhook signal executed: %s %s %s qty=%d price=%.2f (strategy=%s)",
        order["order_id"][:8],
        action,
        symbol,
        order["qty"],
        price,
        strategy,
    )

    # Emit signal event so frontend shows the signal marker on the chart
    if _socketio:
        _socketio.emit("signal", {
            "action": action,
            "symbol": symbol,
            "price": price,
            "strategy": strategy,
            "reason": f"Webhook signal ({strategy})",
        })

    return jsonify({"ok": True, "order": order}), 201
