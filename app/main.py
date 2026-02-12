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
from app.data_feed.demo_feed import DemoDataFeed
from app.data_feed.provider import create_provider
from app.strategy.engine import StrategyEngine
from app.routes.api import api_bp, init_api_deps
from app.routes.webhook import webhook_bp, init_webhook_deps
from app.ws.socket_server import init_socketio
from app.db import storage

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
controller = EngineController()

# --- Capital Manager ---
capital_mgr = CapitalManager(
    initial_capital=INITIAL_CAPITAL,
    max_position_size=MAX_POSITION_SIZE_PER_TRADE,
    max_open_positions=MAX_OPEN_POSITIONS,
    max_exposure_pct=MAX_TOTAL_EXPOSURE_PERCENT,
    max_qty_per_order=MAX_QTY_PER_ORDER,
    daily_loss_limit=DAILY_LOSS_LIMIT,
)

# --- Order Validator ---
order_validator = OrderValidator(
    capital_manager=capital_mgr,
    cooldown_candles=STRATEGY_COOLDOWN_CANDLES,
    cooldown_seconds=SIGNAL_COOLDOWN_SEC,
    max_qty_per_order=MAX_QTY_PER_ORDER,
)

# --- Simulated broker ---
broker = SimulatedBroker()

# --- Order manager (uses broker + capital manager + validator) ---
order_mgr = OrderManager(
    broker_submit_fn=broker.submit_order,
    capital_mgr=capital_mgr,
    order_validator=order_validator,
)

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

# --- Inject dependencies ---
init_api_deps(engine, order_mgr, current_prices, ml_predict_fn, controller)
init_webhook_deps(order_mgr, socketio)
init_socketio(socketio, engine, order_mgr, current_prices, tick_history, controller)

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
        - SL/TP enforcement only when controller == RUNNING.
        - PnL/position broadcasts only when controller == RUNNING.
        - When STOPPED: prices update, chart updates, but no trading.
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

    while not _tick_thread_stop.is_set() and not data_feed.should_stop:

        # ── Advance validator tick counter (always) ──
        order_validator.tick()

        # ── Emit ticks for EVERY symbol (market data always streams) ──
        for yf_sym, gen in generators.items():
            try:
                tick = next(gen)
            except StopIteration:
                continue

            # Update shared price map (always — needed for chart)
            current_prices[yf_sym] = tick["price"]

            # Ring buffer for reconnecting clients (always)
            buf = tick_history.setdefault(yf_sym, [])
            buf.append(tick)
            if len(buf) > TICK_HISTORY_SIZE:
                tick_history[yf_sym] = buf[-TICK_HISTORY_SIZE:]

            # Persist candle to DB (always — chart needs this on reload)
            try:
                candle = {
                    "symbol": yf_sym,
                    "timeframe": "tick",
                    "timestamp": int(_time_mod.time()),
                    "open": tick.get("open", tick["price"]),
                    "high": tick.get("high", tick["price"]),
                    "low": tick.get("low", tick["price"]),
                    "close": tick["price"],
                    "volume": tick.get("volume", 0),
                }
                storage.insert_or_update_candle(candle)
            except Exception as exc:
                logger.debug("Candle persistence error: %s", exc)

            # Emit tick to all clients (always — chart updates regardless)
            socketio.emit("tick", tick)

            # ── STRATEGY: only when RUNNING ──
            if controller.is_running:
                signal = engine.on_tick(tick)
                if signal:
                    socketio.emit("signal", signal)

            socketio.sleep(0.02)

        # ── SL/TP enforcement: only when RUNNING ──
        if controller.is_running:
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
                        "timestamp": o["created_at"],
                    },
                )

        # ── PnL/positions: only when RUNNING ──
        _pnl_counter += 1
        if _pnl_counter % 2 == 0:
            if controller.is_running:
                pnl_data = order_mgr.get_pnl(current_prices=current_prices)
                socketio.emit("pnl_update", pnl_data)
                positions = order_mgr.get_positions()
                socketio.emit("position_update", {"positions": positions})

        # ── Periodic cleanup (always) ──
        _cleanup_counter += 1
        if _cleanup_counter % 30 == 0:
            timed_out = order_mgr.cleanup_stale_orders()
            if timed_out:
                logger.info("Cleaned up %d stale orders", timed_out)

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
        init_api_deps(engine, order_mgr, current_prices, ml_predict_fn, controller)

    broker.start()
    socketio.start_background_task(_tick_loop, DEFAULT_SYMBOLS)

    logger.info("Starting server on %s:%d  ML=%s", args.host, args.port, use_ml)
    socketio.run(app, host=args.host, port=args.port, debug=FLASK_DEBUG)


if __name__ == "__main__":
    main()
