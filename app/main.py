"""
app/main.py
===========
Flask application entry point.

Architecture
------------
    EngineController — single source of truth for engine state (IDLE/RUNNING/STOPPED/PAUSED)
    StrategyEngine   — strategy logic, guarded by controller
    CapitalManager   — capital/position tracking
    OrderValidator   — pre-trade validation
    OrderManager     — order lifecycle (submit, fill, SL/TP)
    SimulatedBroker  — fake exchange with latency/slippage
    DemoDataFeed     — historical CSV replayed as ticks

Tick loop design:
    Market data (ticks, candles) ALWAYS streams regardless of engine state.
    Strategy signals, SL/TP, PnL updates are ONLY processed when
    controller.state == RUNNING.

Run::

    python -m app.main [--use-ml]
"""

# --- eventlet monkey-patching MUST happen before any other imports ---
# This patches stdlib threading, socket, time, etc. so that
# Flask-SocketIO background tasks can emit to WebSocket clients.
import eventlet

eventlet.monkey_patch()

import argparse
import logging
import logging.handlers
import os
import sys
import threading
import time as _time_mod
from pathlib import Path

# --- Ensure project root is on sys.path so ``app.*`` imports work ---
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from flask import Flask, send_from_directory
from flask_cors import CORS
from flask_socketio import SocketIO

from app.config import (
    DEFAULT_SYMBOLS,
    FLASK_DEBUG,
    FLASK_HOST,
    FLASK_PORT,
    INITIAL_CAPITAL,
    LOG_FILE,
    LOG_LEVEL,
    ML_ENABLED,
    SECRET_KEY,
    TICK_INTERVAL_SEC,
    PROJECT_ROOT,
    MAX_OPEN_POSITIONS,
    DAILY_LOSS_LIMIT,
    MAX_POSITION_SIZE_PER_TRADE,
    MAX_TOTAL_EXPOSURE_PERCENT,
    MAX_QTY_PER_ORDER,
    STRATEGY_COOLDOWN_CANDLES,
    SIGNAL_COOLDOWN_SEC,
    MODE,
    PNL_SNAPSHOT_INTERVAL,
)

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------


def _setup_logging() -> None:
    log_dir = Path(LOG_FILE).parent
    log_dir.mkdir(parents=True, exist_ok=True)
    handlers = [
        logging.StreamHandler(sys.stdout),
        logging.handlers.RotatingFileHandler(
            LOG_FILE, maxBytes=5_000_000, backupCount=3
        ),
    ]
    logging.basicConfig(
        level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=handlers,
    )


_setup_logging()
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Create Flask + SocketIO
# ---------------------------------------------------------------------------

app = Flask(
    __name__,
    static_folder=str(PROJECT_ROOT / "frontend" / "static"),
    static_url_path="/static",
)
app.config["SECRET_KEY"] = SECRET_KEY
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="eventlet")

# ---------------------------------------------------------------------------
# Shared mutable state (thread-safe via locks in respective modules)
# ---------------------------------------------------------------------------

# Latest prices per symbol — updated by tick thread, read by API / engine
current_prices: dict[str, float] = {}

# Ring buffer of recent ticks per symbol — sent to clients on connect so
# the chart shows historical candles immediately instead of starting empty.
TICK_HISTORY_SIZE = 500  # keep last N ticks per symbol
tick_history: dict[str, list] = {}  # {symbol: [tick_dict, ...]}

# ---------------------------------------------------------------------------
# Wire components
# ---------------------------------------------------------------------------

from app.engine_controller import EngineController
from app.broker.capital_manager import CapitalManager
from app.broker.order_validator import OrderValidator
from app.broker.order_manager import OrderManager
from app.broker.simulated_broker import SimulatedBroker
from app.broker.trade_ledger import TradeLedger
from app.data_feed.demo_feed import DemoDataFeed
from app.data_feed.provider import create_provider
from app.strategy.engine import StrategyEngine
from app.routes.api import api_bp, init_api_deps
from app.routes.webhook import webhook_bp, init_webhook_deps
from app.routes.auth import auth_bp
from app.ws.socket_server import init_socketio
from app.db import storage
from app.utils.clock import EngineClock
from app.utils.candle_aggregator import CandleAggregator

# --- ML (optional) ---
ml_predict_fn = None


def _load_ml():
    global ml_predict_fn
    try:
        from app.ml.predictor import predict_proba

        ml_predict_fn = predict_proba
        logger.info("ML predictor loaded")
    except Exception as exc:
        logger.warning("ML module not available: %s", exc)


# --- Engine Controller (SINGLE SOURCE OF TRUTH for engine state) ---
controller = EngineController(
    persist_fn=storage.update_engine_state,
    restore_fn=storage.get_engine_state,
)

# --- Engine Clock (IST / NSE market hours awareness) ---
engine_clock = EngineClock(mode=MODE)

# --- Candle Aggregator (timeframe-aligned candles) ---
candle_aggregator = CandleAggregator(clock=engine_clock, timeframe="1m")

# --- Trade Ledger (single source of truth for PnL from trades table) ---
trade_ledger = TradeLedger(account_id="default")

# --- Capital Manager ---
capital_mgr = CapitalManager(
    initial_capital=INITIAL_CAPITAL,
    max_position_size=MAX_POSITION_SIZE_PER_TRADE,
    max_open_positions=MAX_OPEN_POSITIONS,
    max_exposure_pct=MAX_TOTAL_EXPOSURE_PERCENT,
    max_qty_per_order=MAX_QTY_PER_ORDER,
    daily_loss_limit=DAILY_LOSS_LIMIT,
)

# --- Startup PnL Verification ---
# Verify CapitalManager state against TradeLedger (single source of truth)
try:
    verification = trade_ledger.verify_against_capital_manager(capital_mgr)
    if not verification["match"]:
        logger.warning(
            "PnL mismatch on startup: ledger=%.2f, capital_mgr=%.2f, diff=%.2f",
            verification["ledger_pnl"],
            verification["capital_manager_pnl"],
            verification["difference"],
        )
        # Auto-correct: rebuild capital state from trades table
        rebuild = trade_ledger.rebuild_capital_from_trades(INITIAL_CAPITAL)
        logger.info(
            "Rebuilt capital from trades: realised_pnl=%.2f, available=%.2f",
            rebuild["realised_pnl"],
            rebuild["available_capital"],
        )
    else:
        logger.info(
            "PnL verification passed on startup (%.2f)", verification["ledger_pnl"]
        )
except Exception as exc:
    logger.warning("Startup PnL verification failed: %s", exc)

# --- Order Validator ---
order_validator = OrderValidator(
    capital_manager=capital_mgr,
    cooldown_candles=STRATEGY_COOLDOWN_CANDLES,
    cooldown_seconds=SIGNAL_COOLDOWN_SEC,
    max_qty_per_order=MAX_QTY_PER_ORDER,
)


# --- Simulated broker ---
# Use a direct in-process callback instead of HTTP webhook.
# The webhook approach fails because the broker's threading.Thread
# issues HTTP POSTs back to the same server, which can deadlock
# or silently fail under eventlet.
def _broker_on_update(payload):
    """Direct callback from SimulatedBroker — runs in broker thread.

    IMPORTANT: We schedule the actual SocketIO emit onto the eventlet
    hub via ``socketio.start_background_task`` so that the emit
    reliably reaches connected clients (cross-thread emit under
    eventlet can silently fail otherwise).
    """
    order_id = payload.get("order_id")
    new_status = payload.get("status", "")
    filled_qty = int(payload.get("filled_qty", 0))
    avg_price = float(payload.get("avg_price", 0.0))

    mgr = _broker_on_update._order_mgr
    if mgr is None:
        return

    updated = mgr.update_order_status(
        order_id, new_status, filled_qty=filled_qty, avg_price=avg_price
    )
    if updated is None:
        return

    # Build the payload once, then emit from the eventlet hub
    ts_now = engine_clock.now_iso()
    order_data = {
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
        "timestamp": ts_now,
    }

    def _do_emit():
        """Runs on the eventlet hub — safe to call socketio.emit here."""
        socketio.emit("order_update", order_data)
        logger.debug("Order update emitted: %s → %s", order_id[:8], new_status)

        # Also broadcast position + PnL updates
        positions = mgr.get_positions()
        socketio.emit("position_update", {"positions": positions, "timestamp": ts_now})
        try:
            pnl = mgr.get_pnl(current_prices=current_prices)
            pnl["timestamp"] = ts_now
            socketio.emit("pnl_update", pnl)
        except Exception:
            pass

    socketio.start_background_task(_do_emit)


_broker_on_update._order_mgr = None  # set after order_mgr is created

broker = SimulatedBroker(on_update=_broker_on_update)

# --- Order manager (uses broker + capital manager + validator) ---
order_mgr = OrderManager(
    broker_submit_fn=broker.submit_order,
    capital_mgr=capital_mgr,
    order_validator=order_validator,
)

# Wire the broker callback to the order manager
_broker_on_update._order_mgr = order_mgr

# --- Strategy engine (delegates state to controller) ---
engine = StrategyEngine(
    order_callback=order_mgr.handle_signal,
    ml_predict_fn=None,
    use_ml=False,
    capital_mgr=capital_mgr,
    controller=controller,
)

# --- Wire daily-loss kill switch ---
order_mgr._engine_stop_fn = engine.stop

# --- Data feed ---
data_feed = DemoDataFeed(
    symbols=DEFAULT_SYMBOLS,
    tick_interval=TICK_INTERVAL_SEC,
)

# --- Market data provider (abstraction layer) ---
# The provider is the single interface for historical data and live subscriptions.
# In demo mode we use YahooProvider (or synthetic fallback).
# In live mode this would be ZerodhaProvider.
market_data_provider = create_provider(MODE)
logger.info("Market data provider: %s (mode=%s)", market_data_provider.name, MODE)

# --- Register blueprints ---
app.register_blueprint(api_bp)
app.register_blueprint(webhook_bp)
app.register_blueprint(auth_bp)

# --- Inject dependencies ---
init_api_deps(
    engine,
    order_mgr,
    current_prices,
    ml_predict_fn,
    controller,
    engine_clock,
    trade_ledger,
    capital_mgr,
)
init_webhook_deps(order_mgr, socketio, current_prices)
init_socketio(
    socketio, engine, order_mgr, current_prices, tick_history, controller, engine_clock
)

# ---------------------------------------------------------------------------
# Frontend serving
# ---------------------------------------------------------------------------


@app.route("/")
def index():
    return send_from_directory(str(PROJECT_ROOT / "frontend"), "index.html")


@app.route("/favicon.ico")
def favicon():
    return "", 204


# ---------------------------------------------------------------------------
# Tick streaming background thread
# ---------------------------------------------------------------------------

_tick_thread_stop = threading.Event()


def _tick_loop(symbols: list[str]) -> None:
    """
    Background thread: emit ticks for ALL symbols every cycle.

    CRITICAL DESIGN:
        - Market data (ticks + candle persistence) ALWAYS runs.
        - Strategy signals are ONLY processed when controller == RUNNING.
        - SL/TP enforcement ALWAYS runs (protects positions even when STOPPED).
        - PnL/position broadcasts ALWAYS run.
        - When STOPPED: prices update, chart updates, SL/TP enforced, but no new strategy signals.
    """
    if MODE != "demo":
        logger.warning(
            "MODE=%s — synthetic tick loop disabled. "
            "Attach a live market-data provider to push ticks.",
            MODE,
        )
        return

    data_feed.connect()
    generators = data_feed.create_generators()

    logger.info("Tick loop started for %s", list(generators.keys()))
    _pnl_counter = 0
    _cleanup_counter = 0
    _snapshot_counter = 0

    while not _tick_thread_stop.is_set() and not data_feed.should_stop:

        # ── Advance validator tick counter (always) ──
        order_validator.tick()

        # ── Get authoritative timestamp for this tick cycle ──
        cycle_ts = engine_clock.now_iso()
        cycle_epoch = engine_clock.epoch()

        # ── Emit ticks for EVERY symbol (market data always streams) ──
        for yf_sym, gen in generators.items():
            try:
                tick = next(gen)
            except StopIteration:
                continue

            # Stamp tick with authoritative UTC timestamp
            tick["timestamp"] = cycle_ts

            # Update shared price map (always — needed for chart)
            current_prices[yf_sym] = tick["price"]

            # Ring buffer for reconnecting clients (always)
            buf = tick_history.setdefault(yf_sym, [])
            buf.append(tick)
            if len(buf) > TICK_HISTORY_SIZE:
                tick_history[yf_sym] = buf[-TICK_HISTORY_SIZE:]

            # ── Candle aggregation (timeframe-aligned) ──
            try:
                completed = candle_aggregator.on_tick(
                    yf_sym, tick["price"], tick.get("volume", 0), cycle_epoch
                )
                if completed:
                    storage.upsert_candle(completed)

                # Also persist a raw "tick" candle for chart compatibility
                raw_candle = {
                    "symbol": yf_sym,
                    "timeframe": "tick",
                    "timestamp": cycle_epoch,
                    "open": tick.get("open", tick["price"]),
                    "high": tick.get("high", tick["price"]),
                    "low": tick.get("low", tick["price"]),
                    "close": tick["price"],
                    "volume": tick.get("volume", 0),
                }
                storage.insert_or_update_candle(raw_candle)
            except Exception as exc:
                logger.debug("Candle persistence error: %s", exc)

            # Emit tick to all clients (always — chart updates regardless)
            socketio.emit("tick", tick)

            # ── STRATEGY: only when RUNNING and market is open ──
            if controller.is_running and engine_clock.is_market_open():
                signal = engine.on_tick(tick)
                if signal:
                    signal["timestamp"] = cycle_ts
                    socketio.emit("signal", signal)

            socketio.sleep(0.02)

        # ── SL/TP enforcement: ALWAYS run (protects positions even when STOPPED) ──
        sl_tp_orders = order_mgr.check_sl_tp(current_prices)
        for o in sl_tp_orders:
            socketio.emit(
                "signal",
                {
                    "action": o["side"],
                    "symbol": o["symbol"],
                    "price": o["price"],
                    "reason": f"Auto {o['strategy']} exit",
                    "strategy": o["strategy"],
                    "timestamp": engine_clock.now_iso(),
                },
            )

        # ── PnL/positions: ALWAYS broadcast ──
        # Mark-to-market accounting must reflect live prices regardless
        # of engine state.  Open positions change value as prices move;
        # every real trading terminal shows this.  Stopping the engine
        # only halts NEW signal generation, not portfolio tracking.
        _pnl_counter += 1
        if _pnl_counter % 2 == 0:
            pnl_ts = engine_clock.now_iso()
            pnl_data = order_mgr.get_pnl(current_prices=current_prices)
            pnl_data["engine_running"] = controller.is_running
            pnl_data["timestamp"] = pnl_ts
            socketio.emit("pnl_update", pnl_data)
            positions = order_mgr.get_positions()
            socketio.emit(
                "position_update", {"positions": positions, "timestamp": pnl_ts}
            )
            socketio.sleep(0)  # yield so eventlet flushes the frames

        # ── Periodic cleanup (always) ──
        _cleanup_counter += 1
        if _cleanup_counter % 30 == 0:
            timed_out = order_mgr.cleanup_stale_orders()
            if timed_out:
                logger.info("Cleaned up %d stale orders", timed_out)

        # ── PnL snapshot to DB (periodic) ──
        _snapshot_counter += 1
        if _snapshot_counter % PNL_SNAPSHOT_INTERVAL == 0:
            try:
                snap = order_mgr.get_pnl(current_prices=current_prices)
                snap["timestamp"] = engine_clock.now_iso()
                storage.insert_pnl_snapshot(snap)
            except Exception as exc:
                logger.debug("PnL snapshot error: %s", exc)

        socketio.sleep(max(0.05, TICK_INTERVAL_SEC - 0.06))

    data_feed.disconnect()
    logger.info("Tick loop stopped")


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description="Algo Trading Terminal")
    parser.add_argument("--use-ml", action="store_true", help="Enable ML module")
    parser.add_argument("--host", default=FLASK_HOST)
    parser.add_argument("--port", type=int, default=FLASK_PORT)
    args = parser.parse_args()

    use_ml = args.use_ml or ML_ENABLED

    if use_ml:
        _load_ml()
        engine._ml_predict_fn = ml_predict_fn
        engine.use_ml = True
        init_api_deps(
            engine,
            order_mgr,
            current_prices,
            ml_predict_fn,
            controller,
            engine_clock,
            trade_ledger,
            capital_mgr,
        )

    broker.start()
    socketio.start_background_task(_tick_loop, DEFAULT_SYMBOLS)

    logger.info("Starting server on %s:%d  ML=%s", args.host, args.port, use_ml)
    socketio.run(app, host=args.host, port=args.port, debug=FLASK_DEBUG)


if __name__ == "__main__":
    main()
