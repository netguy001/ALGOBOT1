"""
app/routes/webhook.py
=====================
Webhook receiver for simulated broker callbacks.

The simulated broker POSTs order-state updates here.  This handler
validates the payload, updates the order via OrderManager, and
broadcasts the change to connected WebSocket clients.
"""

import logging
from flask import Blueprint, jsonify, request

logger = logging.getLogger(__name__)

webhook_bp = Blueprint("webhook", __name__, url_prefix="/webhook")

# Injected at startup
_order_mgr = None
_socketio = None


def init_webhook_deps(order_mgr, socketio):
    """Inject order manager and SocketIO instance (called from main.py)."""
    global _order_mgr, _socketio
    _order_mgr = order_mgr
    _socketio = socketio


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
                "status": updated["status"],
                "strategy": updated.get("strategy", ""),
                "stop_loss": updated.get("stop_loss", 0),
                "take_profit": updated.get("take_profit", 0),
                "updated_at": updated["updated_at"],
            },
        )

        # Also broadcast position update
        positions = _order_mgr.get_positions()
        _socketio.emit("position_update", {"positions": positions})

    return jsonify({"ok": True}), 200
