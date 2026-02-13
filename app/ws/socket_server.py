"""
app/ws/socket_server.py
=======================
Flask-SocketIO event handlers.

All control actions (start/stop/set_strategy) go through the EngineController
so that engine state is always consistent.

Events emitted by the server:
    tick            — per-tick price data
    order_update    — order state changes
    position_update — position changes
    status          — engine state snapshot

Events accepted from the UI:
    control         — start/stop/switch strategy/pause
    ping            — keep-alive
    subscribe       — change active symbol on this client
    request_state   — full state dump
"""

import logging
from flask_socketio import SocketIO, emit

logger = logging.getLogger(__name__)

# Will be initialised by init_socketio()
_engine = None
_order_mgr = None
_current_prices = {}
_tick_history = {}
_controller = None  # EngineController
_clock = None  # EngineClock


def init_socketio(
    socketio: SocketIO,
    engine,
    order_mgr,
    current_prices_ref,
    tick_history_ref=None,
    controller=None,
    clock=None,
):
    """Register SocketIO event handlers."""
    global _engine, _order_mgr, _current_prices, _tick_history, _controller, _clock
    _engine = engine
    _order_mgr = order_mgr
    _current_prices = current_prices_ref
    _tick_history = tick_history_ref or {}
    _controller = controller
    _clock = clock

    @socketio.on("connect")
    def handle_connect():
        logger.info("WebSocket client connected")
        emit("status", _build_status())

    @socketio.on("disconnect")
    def handle_disconnect():
        logger.info("WebSocket client disconnected")

    @socketio.on("control")
    def handle_control(data):
        """
        Accept control commands from the UI.

        data: {"action": "start"|"stop"|"pause"|"set_strategy"|"toggle_ml",
               "strategy": "sma_crossover", "use_ml": true}
        """
        action = data.get("action", "")
        logger.info("Control event: %s  payload=%s", action, data)

        if action == "start":
            strategy = data.get("strategy")
            if strategy:
                _engine.set_strategy(strategy)
            _engine.start()  # delegates to controller
        elif action == "stop":
            _engine.stop()  # delegates to controller
            # Emit a final PnL/position snapshot so UI freezes on correct values
            try:
                pnl = _order_mgr.get_pnl(current_prices=_current_prices)
                socketio.emit("pnl_update", pnl)
                positions = _order_mgr.get_positions()
                socketio.emit("position_update", {"positions": positions})
            except Exception:
                pass
        elif action == "pause":
            if _controller:
                _controller.pause(reason="user_pause")
        elif action == "set_strategy":
            _engine.set_strategy(data.get("strategy", "sma_crossover"))
        elif action == "toggle_ml":
            _engine.use_ml = bool(data.get("use_ml", False))
        else:
            logger.warning("Unknown control action: %s", action)

        emit("status", _build_status())

    @socketio.on("ping")
    def handle_ping():
        emit("pong", {"msg": "alive"})

    @socketio.on("request_state")
    def handle_request_state():
        """Client asks for full current state on (re)connect."""
        emit("status", _build_status())

        # Send accumulated tick history for chart
        if _tick_history:
            emit("tick_history", _tick_history)

        positions = _order_mgr.get_positions()
        emit("position_update", {"positions": positions})
        pnl = _order_mgr.get_pnl(current_prices=_current_prices)
        emit("pnl_update", pnl)

        # Send current orders so the order table is populated immediately
        orders = _order_mgr.get_all_orders()
        emit("orders_snapshot", {"orders": orders})


def _build_status() -> dict:
    """Merge engine status with controller state and clock info."""
    st = _engine.status()
    if _controller:
        st["state"] = _controller.state.value
        st["running"] = _controller.is_running
    from app.config import MODE

    st["mode"] = MODE
    if _clock:
        st["market_open"] = _clock.is_market_open()
        st["ist_time"] = _clock.now().strftime("%H:%M:%S")
        st["utc_timestamp"] = _clock.now_iso()
    return st
