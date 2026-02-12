"""
app/routes/api.py
=================
REST API endpoints for the algorithmic trading demo.

All endpoints return JSON.  Input validation is inline for simplicity.
"""

import logging
from flask import Blueprint, jsonify, request

logger = logging.getLogger(__name__)

api_bp = Blueprint("api", __name__, url_prefix="/api")

# These are set at app startup via init_api_deps()
_engine = None
_order_mgr = None
_current_prices: dict[str, float] = {}
_ml_predict_fn = None


def init_api_deps(engine, order_mgr, current_prices_ref, ml_predict_fn=None):
    """Inject dependencies from main.py (avoids circular imports)."""
    global _engine, _order_mgr, _current_prices, _ml_predict_fn
    _engine = engine
    _order_mgr = order_mgr
    _current_prices = current_prices_ref
    _ml_predict_fn = ml_predict_fn


# ------------------------------------------------------------------
# Engine control
# ------------------------------------------------------------------


@api_bp.route("/start", methods=["POST"])
def start_strategy():
    """Start the strategy engine.  Optionally accepts {"strategy": "...", "symbol": "..."}."""
    body = request.get_json(silent=True) or {}
    strategy = body.get("strategy")
    if strategy:
        _engine.set_strategy(strategy)
    _engine.start()
    return jsonify({"status": "started", "strategy": _engine.strategy_name}), 200


@api_bp.route("/stop", methods=["POST"])
def stop_strategy():
    """Stop the strategy engine."""
    _engine.stop()
    return jsonify({"status": "stopped"}), 200


@api_bp.route("/status", methods=["GET"])
def get_status():
    """Return engine status, active strategy, tick count."""
    return jsonify(_engine.status()), 200


# ------------------------------------------------------------------
# Positions & PnL
# ------------------------------------------------------------------


@api_bp.route("/positions", methods=["GET"])
def get_positions():
    positions = _order_mgr.get_positions()
    # Convert to list for JSON
    result = []
    for sym, pos in positions.items():
        result.append(
            {
                "symbol": sym,
                "qty": pos["qty"],
                "avg_price": round(pos["avg_price"], 2),
                "side": pos["side"],
                "current_price": _current_prices.get(sym, 0),
            }
        )
    return jsonify({"positions": result}), 200


@api_bp.route("/pnl", methods=["GET"])
def get_pnl():
    pnl = _order_mgr.get_pnl(current_prices=_current_prices)
    return jsonify(pnl), 200


# ------------------------------------------------------------------
# Orders
# ------------------------------------------------------------------


@api_bp.route("/place-order", methods=["POST"])
def place_order():
    """
    Place a manual order.

    Body::

        {"symbol": "RELIANCE.NS", "side": "BUY", "qty": 10, "price": 2500}
    """
    body = request.get_json(silent=True)
    if not body:
        return jsonify({"error": "JSON body required"}), 400

    required = ("symbol", "side", "qty", "price")
    missing = [f for f in required if f not in body]
    if missing:
        return jsonify({"error": f"Missing fields: {missing}"}), 400

    if body["side"].upper() not in ("BUY", "SELL"):
        return jsonify({"error": "side must be BUY or SELL"}), 400

    try:
        qty = int(body["qty"])
        price = float(body["price"])
    except (ValueError, TypeError):
        return jsonify({"error": "qty must be int, price must be float"}), 400

    order = _order_mgr.place_manual_order(
        symbol=body["symbol"],
        side=body["side"],
        qty=qty,
        price=price,
    )
    return jsonify({"order": order}), 201


@api_bp.route("/cancel-order", methods=["POST"])
def cancel_order():
    """Cancel an order.  Body: {"order_id": "..."}"""
    body = request.get_json(silent=True)
    if not body or "order_id" not in body:
        return jsonify({"error": "order_id required"}), 400

    ok = _order_mgr.cancel_order(body["order_id"])
    if ok:
        return jsonify({"status": "cancelled", "order_id": body["order_id"]}), 200
    return jsonify({"error": "Cannot cancel order (not open or not found)"}), 400


@api_bp.route("/orders", methods=["GET"])
def get_orders():
    """Return recent orders."""
    from app.db import storage

    orders = storage.get_all_orders(limit=100)
    return jsonify({"orders": orders}), 200


# ------------------------------------------------------------------
# ML prediction (standalone endpoint)
# ------------------------------------------------------------------


@api_bp.route("/ml-predict", methods=["GET"])
def ml_predict():
    """
    Return ML prediction for a symbol.

    Query params: ?symbol=RELIANCE.NS
    """
    symbol = request.args.get("symbol", "RELIANCE.NS")
    price = _current_prices.get(symbol, 0)

    if _ml_predict_fn is None:
        return jsonify({"error": "ML module not loaded", "ml_enabled": False}), 200

    try:
        prob = _ml_predict_fn(symbol, price)
        return (
            jsonify(
                {
                    "symbol": symbol,
                    "price": price,
                    "probability_up": round(prob, 4) if prob is not None else None,
                    "ml_enabled": True,
                }
            ),
            200,
        )
    except Exception as exc:
        return jsonify({"error": str(exc), "ml_enabled": True}), 500
