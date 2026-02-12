"""
app/ws/socket_server.py
=======================
Flask-SocketIO event handlers.

Events emitted by the server:
    tick            — per-tick price data
    order_update    — order state changes
    position_update — position changes

Events accepted from the UI:
    control         — start/stop/switch strategy
    ping            — keep-alive
"""

import logging
from flask_socketio import SocketIO, emit

logger = logging.getLogger(__name__)

# Will be initialised by init_socketio()
_engine = None
_order_mgr = None
_current_prices = {}


def init_socketio(socketio: SocketIO, engine, order_mgr, current_prices_ref):
    """Register SocketIO event handlers."""
    global _engine, _order_mgr, _current_prices
    _engine = engine
    _order_mgr = order_mgr
    _current_prices = current_prices_ref

    @socketio.on("connect")
    def handle_connect():
        logger.info("WebSocket client connected")
        emit("status", _engine.status())

    @socketio.on("disconnect")
    def handle_disconnect():
        logger.info("WebSocket client disconnected")

    @socketio.on("control")
    def handle_control(data):
        """
        Accept control commands from the UI.

        data: {"action": "start"|"stop"|"set_strategy"|"toggle_ml",
               "strategy": "sma_crossover", "use_ml": true}
        """
        action = data.get("action", "")
        logger.info("Control event: %s  payload=%s", action, data)

        if action == "start":
            strategy = data.get("strategy")
            if strategy:
                _engine.set_strategy(strategy)
            _engine.start()
        elif action == "stop":
            _engine.stop()
        elif action == "set_strategy":
            _engine.set_strategy(data.get("strategy", "sma_crossover"))
        elif action == "toggle_ml":
            _engine.use_ml = bool(data.get("use_ml", False))
        else:
            logger.warning("Unknown control action: %s", action)

        emit("status", _engine.status())

    @socketio.on("ping")
    def handle_ping():
        emit("pong", {"msg": "alive"})

    @socketio.on("request_state")
    def handle_request_state():
        """Client asks for full current state on reconnect."""
        emit("status", _engine.status())
        positions = _order_mgr.get_positions()
        emit("position_update", {"positions": positions})
        pnl = _order_mgr.get_pnl(current_prices=_current_prices)
        emit("pnl_update", pnl)
