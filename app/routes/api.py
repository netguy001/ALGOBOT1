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
_controller = None  # EngineController
_clock = None  # EngineClock
_ledger = None  # TradeLedger
_capital_mgr = None  # CapitalManager


def init_api_deps(
    engine,
    order_mgr,
    current_prices_ref,
    ml_predict_fn=None,
    controller=None,
    clock=None,
    ledger=None,
    capital_mgr=None,
):
    """Inject dependencies from main.py (avoids circular imports)."""
    global _engine, _order_mgr, _current_prices, _ml_predict_fn, _controller, _clock
    global _ledger, _capital_mgr
    _engine = engine
    _order_mgr = order_mgr
    _current_prices = current_prices_ref
    _ml_predict_fn = ml_predict_fn
    _controller = controller
    _clock = clock
    _ledger = ledger
    _capital_mgr = capital_mgr


# ------------------------------------------------------------------
# Candles (historical chart data — persisted in SQLite)
# ------------------------------------------------------------------


@api_bp.route("/candles", methods=["GET"])
def get_candles():
    """Return historical candles for a symbol/timeframe.

    Query params:
        symbol    — e.g. RELIANCE.NS  (required)
        timeframe — e.g. tick, 1m      (default: tick)
        limit     — max rows           (default: 500)

    Returns candles sorted ascending by timestamp so the frontend can
    load them directly into the chart without re-sorting.
    """
    from app.db import storage

    symbol = request.args.get("symbol", "")
    if not symbol:
        return jsonify({"error": "symbol query param required"}), 400

    timeframe = request.args.get("timeframe", "tick")
    try:
        limit = int(request.args.get("limit", "500"))
    except (ValueError, TypeError):
        limit = 500
    limit = min(max(limit, 1), 5000)  # clamp 1-5000

    candles = storage.get_recent_candles(symbol, timeframe, limit)
    return jsonify({"candles": candles, "count": len(candles)}), 200


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
    """Stop the strategy engine.  Freezes strategy + PnL updates."""
    _engine.stop()
    state = _controller.state.value if _controller else "STOPPED"
    return jsonify({"status": "stopped", "state": state}), 200


@api_bp.route("/status", methods=["GET"])
def get_status():
    """Return engine status, active strategy, tick count, and trading mode."""
    from app.config import MODE

    status = _engine.status()
    status["mode"] = MODE
    if _controller:
        status["state"] = _controller.state.value
        status["running"] = _controller.is_running
    if _clock:
        status["market_open"] = _clock.is_market_open()
        status["ist_time"] = _clock.now().strftime("%H:%M:%S")
        status["utc_timestamp"] = _clock.now_iso()
    return jsonify(status), 200


@api_bp.route("/clock", methods=["GET"])
def get_clock():
    """Return EngineClock state — IST time, market open status, etc."""
    if _clock:
        return jsonify(_clock.to_dict()), 200
    return jsonify({"error": "Clock not available"}), 503


# ------------------------------------------------------------------
# Positions & PnL
# ------------------------------------------------------------------


@api_bp.route("/positions", methods=["GET"])
def get_positions():
    positions = _order_mgr.get_positions()
    # Convert to list for JSON — skip FLAT positions
    result = []
    for sym, pos in positions.items():
        if pos.get("qty", 0) <= 0:
            continue
        cp = _current_prices.get(sym, 0)
        result.append(
            {
                "symbol": sym,
                "qty": pos["qty"],
                "avg_price": round(pos["avg_price"], 2),
                "side": pos["side"],
                "current_price": cp,
            }
        )
    return jsonify({"positions": result}), 200


@api_bp.route("/pnl", methods=["GET"])
def get_pnl():
    pnl = _order_mgr.get_pnl(current_prices=_current_prices)
    # Attach ledger verification
    if _ledger and _capital_mgr:
        ledger_pnl = _ledger.compute_pnl(_capital_mgr.initial_capital, _current_prices)
        pnl["ledger_realised"] = ledger_pnl["realised_pnl"]
        pnl["ledger_trade_count"] = ledger_pnl["trade_count"]
    return jsonify(pnl), 200


@api_bp.route("/ledger", methods=["GET"])
def get_ledger():
    """Return trade-ledger PnL — computed exclusively from trades table."""
    if not _ledger or not _capital_mgr:
        return jsonify({"error": "Ledger not available"}), 503
    pnl = _ledger.compute_pnl(_capital_mgr.initial_capital, _current_prices)
    # Optionally verify against CapitalManager
    verification = _ledger.verify_against_capital_manager(_capital_mgr)
    pnl["verification"] = verification
    return jsonify(pnl), 200


@api_bp.route("/account", methods=["GET"])
def get_account():
    """Return account state (capital, realised PnL) from DB."""
    from app.db import storage

    acct = storage.get_account("default")
    if not acct:
        return jsonify({"error": "No account found"}), 404
    pnl = _order_mgr.get_pnl(current_prices=_current_prices)
    acct_data = dict(acct)
    acct_data["unrealised_pnl"] = pnl.get("unrealised_pnl", 0)
    acct_data["total_pnl"] = pnl.get("total_pnl", 0)
    return jsonify(acct_data), 200


@api_bp.route("/account/reset", methods=["POST"])
def reset_account():
    """Reset account to initial capital.  Clears positions but keeps order/trade history."""
    from app.db import storage
    from app.config import INITIAL_CAPITAL

    storage.reset_account("default", INITIAL_CAPITAL)
    return jsonify({"status": "reset", "initial_capital": INITIAL_CAPITAL}), 200


@api_bp.route("/equity-history", methods=["GET"])
def get_equity_history():
    """
    Return equity curve data (PnL snapshots over time).

    Query params:
        - limit: max number of records (default 500)

    Returns list of snapshots with timestamp, capital, realised_pnl, unrealised_pnl, total_pnl.
    """
    from app.db import storage

    limit = request.args.get("limit", 500, type=int)
    limit = min(max(1, limit), 5000)  # clamp to reasonable range
    snapshots = storage.get_pnl_history(limit=limit)
    # Reverse to chronological order (oldest first) for charting
    snapshots.reverse()
    return jsonify({"equity_history": snapshots, "count": len(snapshots)}), 200


@api_bp.route("/drawdown", methods=["GET"])
def get_drawdown():
    """
    Return current drawdown metrics.

    Returns:
        - current_equity: Current account equity
        - peak_equity: Peak equity (initial capital)
        - drawdown_value: Absolute drawdown
        - drawdown_pct: Percentage drawdown
    """
    if not _capital_mgr:
        return jsonify({"error": "Capital manager not available"}), 503
    drawdown = _capital_mgr.get_drawdown(_current_prices)
    return jsonify(drawdown), 200


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

    # Gate manual orders when engine is stopped (unless config allows it)
    from app.config import ALLOW_MANUAL_WHEN_STOPPED

    if _controller and not _controller.is_running and not ALLOW_MANUAL_WHEN_STOPPED:
        return jsonify({"error": "Engine is stopped — manual orders disabled"}), 400

    try:
        qty = int(body["qty"])
        raw_price = float(body["price"])
    except (ValueError, TypeError):
        return jsonify({"error": "qty must be int, price must be float"}), 400

    # Resolve price for MARKET orders — use current market price when 0
    price = raw_price
    if price <= 0:
        price = _current_prices.get(body["symbol"], 0.0)
        if price <= 0:
            return jsonify({"error": f"No market price for {body['symbol']}"}), 400

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
        # Return the full cancelled order so frontend can update immediately
        cancelled = _order_mgr._orders.get(body["order_id"], {})
        return (
            jsonify(
                {
                    "status": "cancelled",
                    "order_id": body["order_id"],
                    "order": cancelled,
                }
            ),
            200,
        )
    return jsonify({"error": "Cannot cancel order (not open or not found)"}), 400


@api_bp.route("/orders", methods=["GET"])
def get_orders():
    """Return recent orders with pagination.

    Query params:
        limit  — max rows (default 50, max 500)
        offset — row offset for pagination (default 0)
    """
    from app.db import storage

    try:
        limit = min(int(request.args.get("limit", "50")), 500)
        offset = max(int(request.args.get("offset", "0")), 0)
    except (ValueError, TypeError):
        limit, offset = 50, 0

    orders = storage.get_all_orders(limit=limit, offset=offset)
    return jsonify({"orders": orders, "limit": limit, "offset": offset}), 200


@api_bp.route("/trades", methods=["GET"])
def get_trades():
    """Return recent filled trades from DB.

    Query params:
        limit — max rows (default 100, max 500)
    """
    from app.db import storage

    try:
        limit = min(int(request.args.get("limit", "100")), 500)
    except (ValueError, TypeError):
        limit = 100

    trades = storage.get_trades(limit=limit)
    return jsonify({"trades": trades}), 200


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


# ------------------------------------------------------------------
# Data Source Debug Info
# ------------------------------------------------------------------


@api_bp.route("/datasource", methods=["GET"])
def get_datasource():
    """Return data source info per symbol — CSV date range, source type, mode."""
    from app.config import MODE, DEFAULT_SYMBOLS, DATA_DIR
    from app.utils.data import load_cached_ohlcv, resolve_symbol
    from pathlib import Path

    result = {}
    for sym in DEFAULT_SYMBOLS:
        resolved = resolve_symbol(sym)
        df = load_cached_ohlcv(resolved)
        csv_path = DATA_DIR / f"{resolved.replace('.', '_')}_1d.csv"

        info = {
            "symbol": sym,
            "resolved": resolved,
            "source": "unknown",
            "csv_exists": csv_path.exists(),
            "rows": 0,
            "date_range": None,
        }

        if not df.empty:
            info["rows"] = len(df)
            try:
                first_date = (
                    str(df.index[0].date())
                    if hasattr(df.index[0], "date")
                    else str(df.index[0])
                )
                last_date = (
                    str(df.index[-1].date())
                    if hasattr(df.index[-1], "date")
                    else str(df.index[-1])
                )
                info["date_range"] = f"{first_date} → {last_date}"
            except Exception:
                info["date_range"] = "?"

            # Detect source: if CSV has >250 rows it's likely Yahoo; <250 synthetic
            if len(df) > 200:
                info["source"] = "Yahoo Cached"
            else:
                info["source"] = "Synthetic Generated"
        elif csv_path.exists():
            info["source"] = "CSV (empty)"
        else:
            info["source"] = "None"

        result[sym] = info

    return jsonify({"mode": MODE, "symbols": result}), 200
