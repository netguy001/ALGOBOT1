"""
app/main.py
===========
Flask application entry point.

Wires together: Flask + SocketIO + REST blueprints + strategy engine +
simulated broker + tick simulator.

Run::

    python app/main.py [--use-ml]
"""

import argparse
import logging
import os
import sys
import threading
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
    LOG_FILE,
    LOG_LEVEL,
    ML_ENABLED,
    SECRET_KEY,
    TICK_INTERVAL_SEC,
    PROJECT_ROOT,
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


import logging.handlers  # noqa: E402 (needed before _setup_logging call)

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

# ---------------------------------------------------------------------------
# Wire components
# ---------------------------------------------------------------------------

from app.broker.order_manager import OrderManager
from app.broker.simulated_broker import SimulatedBroker
from app.strategy.engine import StrategyEngine
from app.routes.api import api_bp, init_api_deps
from app.routes.webhook import webhook_bp, init_webhook_deps
from app.ws.socket_server import init_socketio
from app.utils.data import (
    tick_generator,
    resolve_symbol,
    download_ohlcv,
    load_cached_ohlcv,
)
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


# --- Simulated broker ---
broker = SimulatedBroker()

# --- Order manager (uses broker.submit_order as the send function) ---
order_mgr = OrderManager(broker_submit_fn=broker.submit_order)

# --- Strategy engine ---
engine = StrategyEngine(
    order_callback=order_mgr.handle_signal,
    ml_predict_fn=None,  # patched below if ML enabled
    use_ml=False,
)

# --- Register blueprints ---
app.register_blueprint(api_bp)
app.register_blueprint(webhook_bp)

# --- Inject dependencies ---
init_api_deps(engine, order_mgr, current_prices, ml_predict_fn)
init_webhook_deps(order_mgr, socketio)
init_socketio(socketio, engine, order_mgr, current_prices)

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
    Background thread: emit ticks for ALL symbols each cycle.

    Optimisations vs. the original round-robin approach:
     • All symbols tick in a single cycle → lower perceived latency.
     • PnL is emitted once per cycle (not per-symbol) → less bandwidth.
     • Position updates are batched once per cycle.
     • Tiny inter-symbol sleep (0.02s) prevents eventlet starvation
       while keeping the cycle fast.
    """
    generators = {}
    for sym in symbols:
        yf_sym = resolve_symbol(sym)
        generators[yf_sym] = tick_generator(sym, interval_sec=0)

    logger.info("Tick loop started for %s", list(generators.keys()))
    _pnl_counter = 0

    while not _tick_thread_stop.is_set():
        # --- Emit a tick for each symbol in quick succession ---
        for yf_sym, gen in generators.items():
            try:
                tick = next(gen)
            except StopIteration:
                continue

            current_prices[yf_sym] = tick["price"]
            socketio.emit("tick", tick)

            # Feed tick into strategy; emit signal if generated
            signal = engine.on_tick(tick)
            if signal:
                socketio.emit("signal", signal)

            # Yield to eventlet briefly so WS frames flush
            socketio.sleep(0.02)

        # --- Once per cycle: emit PnL + positions (throttled) ---
        _pnl_counter += 1
        if _pnl_counter % 2 == 0:  # every 2nd cycle ≈ 1s
            pnl_data = order_mgr.get_pnl(current_prices=current_prices)
            socketio.emit("pnl_update", pnl_data)
            positions = order_mgr.get_positions()
            socketio.emit("position_update", {"positions": positions})

        # --- Main inter-cycle sleep ---
        socketio.sleep(max(0.05, TICK_INTERVAL_SEC - 0.06))

    logger.info("Tick loop stopped")


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description="Algo Demo India")
    parser.add_argument("--use-ml", action="store_true", help="Enable ML module")
    parser.add_argument("--host", default=FLASK_HOST)
    parser.add_argument("--port", type=int, default=FLASK_PORT)
    args = parser.parse_args()

    use_ml = args.use_ml or ML_ENABLED

    if use_ml:
        _load_ml()
        engine._ml_predict_fn = ml_predict_fn
        engine.use_ml = True
        init_api_deps(engine, order_mgr, current_prices, ml_predict_fn)

    # Ensure we have data for default symbols
    for sym in DEFAULT_SYMBOLS:
        df = load_cached_ohlcv(sym)
        if df.empty:
            logger.info("Downloading data for %s …", sym)
            download_ohlcv(sym)

    # Start simulated broker
    broker.start()

    # Start tick loop in a background thread managed by SocketIO
    socketio.start_background_task(_tick_loop, DEFAULT_SYMBOLS)

    logger.info("Starting server on %s:%d  ML=%s", args.host, args.port, use_ml)
    socketio.run(app, host=args.host, port=args.port, debug=FLASK_DEBUG)


if __name__ == "__main__":
    main()
