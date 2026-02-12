"""
app/strategy/engine.py
======================
Strategy manager — loads strategies, feeds ticks, and routes resulting
trade signals to the order manager.

The engine defers all state decisions to the EngineController.
It does NOT own a ``_running`` bool — it asks the controller.

Engine safety guards (checked every tick cycle):
    - max_open_positions    — refuses signals if limit reached
    - max_total_exposure    — refuses signals if portfolio too exposed
    - max_daily_loss_pct    — auto-stops engine if daily loss breached
    - kill_switch           — immediate hard stop, no exceptions

These guards complement the OrderValidator checks — defence-in-depth.
"""

import logging
import threading
from typing import Optional

from app.strategy.strategies import STRATEGY_REGISTRY, Signal
from app.config import (
    DEFAULT_STRATEGY,
    ML_ENABLED,
    ML_PROBABILITY_THRESHOLD,
    MAX_DAILY_LOSS_PCT,
    KILL_SWITCH,
)

logger = logging.getLogger(__name__)


class StrategyEngine:
    """
    Orchestrates strategy execution.

    Usage::

        engine = StrategyEngine(order_callback=send_order, controller=ctrl)
        engine.set_strategy("sma_crossover")
        engine.on_tick(tick_dict)  # call repeatedly with each tick
    """

    def __init__(
        self,
        order_callback=None,
        ml_predict_fn=None,
        use_ml: bool = ML_ENABLED,
        capital_mgr=None,
        controller=None,
    ):
        self._strategy = None
        self._strategy_name: str = DEFAULT_STRATEGY
        self._lock = threading.Lock()
        self._order_callback = order_callback
        self._ml_predict_fn = ml_predict_fn
        self.use_ml = use_ml
        self._tick_count = 0
        self._capital_mgr = capital_mgr
        self._controller = controller  # EngineController (single source of truth)
        self._halted_reason: Optional[str] = None

        # Respect kill switch from config on init
        if KILL_SWITCH:
            self._halted_reason = "kill_switch_from_config"
            logger.warning(
                "Engine kill switch is ON from config — engine will not start"
            )

        self.set_strategy(self._strategy_name)

    # ------------------------------------------------------------------
    # Strategy selection
    # ------------------------------------------------------------------

    def set_strategy(self, name: str, **kwargs) -> None:
        cls = STRATEGY_REGISTRY.get(name)
        if cls is None:
            logger.error(
                "Unknown strategy: %s. Available: %s",
                name,
                list(STRATEGY_REGISTRY.keys()),
            )
            return
        with self._lock:
            self._strategy = cls(**kwargs)
            self._strategy_name = name
        logger.info("Strategy set to %s", name)

    @property
    def strategy_name(self) -> str:
        return self._strategy_name

    @property
    def running(self) -> bool:
        """Delegates to the EngineController if available."""
        if self._controller:
            return self._controller.is_running
        return False

    # ------------------------------------------------------------------
    # Control — delegates to EngineController
    # ------------------------------------------------------------------

    def start(self) -> None:
        if KILL_SWITCH or (self._capital_mgr and self._capital_mgr.kill_switch):
            self._halted_reason = "kill_switch"
            logger.warning("Cannot start engine — kill switch is active")
            return
        if self._controller:
            self._controller.start(reason="engine.start")
        self._halted_reason = None
        logger.info("Strategy engine started (%s)", self._strategy_name)

    def stop(self) -> None:
        if self._controller:
            self._controller.stop(reason="engine.stop")
        logger.info("Strategy engine stopped")

    def emergency_stop(self, reason: str) -> None:
        if self._controller:
            self._controller.emergency_stop(reason)
        self._halted_reason = reason
        logger.warning("ENGINE EMERGENCY STOP: %s", reason)

    def reset(self) -> None:
        with self._lock:
            if self._strategy:
                self._strategy.reset()
            self._tick_count = 0
        logger.info("Strategy engine reset")

    # ------------------------------------------------------------------
    # Safety guard checks (called every tick before signal processing)
    # ------------------------------------------------------------------

    def _check_safety_guards(self) -> Optional[str]:
        if self._capital_mgr is None:
            return None

        cm = self._capital_mgr

        if cm.kill_switch:
            return "kill_switch"

        if cm.daily_loss_halted:
            return "daily_loss_halted"

        if cm.initial_capital > 0 and cm.realised_pnl < 0:
            daily_loss_pct = abs(cm.realised_pnl) / cm.initial_capital * 100
            if daily_loss_pct >= MAX_DAILY_LOSS_PCT:
                cm.halt()
                return (
                    f"daily_loss_pct ({daily_loss_pct:.1f}% >= {MAX_DAILY_LOSS_PCT}%)"
                )

        if cm.total_exposure >= cm.max_exposure_pct:
            return f"max_exposure ({cm.total_exposure:.1f}%)"

        if cm.open_position_count >= cm.max_open_positions:
            return f"max_open_positions ({cm.open_position_count})"

        return None

    # ------------------------------------------------------------------
    # Tick processing — guarded by EngineController state
    # ------------------------------------------------------------------

    def on_tick(self, tick: dict) -> Optional[Signal]:
        """
        Feed a tick into the active strategy.

        Returns the signal (if any) for callers that need it.
        **Immediately returns None if the controller is not RUNNING.**
        """
        # ── Gate: only process when controller is RUNNING ──
        if self._controller and not self._controller.is_running:
            return None
        if self._strategy is None:
            return None

        self._tick_count += 1

        # --- Engine-level safety guards ---
        breach = self._check_safety_guards()
        if breach:
            self.emergency_stop(breach)
            return None

        with self._lock:
            signal = self._strategy.on_tick(tick)

        if signal is None:
            return None

        # --- Optional ML filter ---
        if self.use_ml and self._ml_predict_fn is not None:
            try:
                prob = self._ml_predict_fn(signal["symbol"], signal["price"])
                if prob is not None:
                    signal["ml_probability"] = round(prob, 4)
                    if signal["action"] == "BUY" and prob < ML_PROBABILITY_THRESHOLD:
                        logger.info(
                            "ML filter blocked BUY signal (prob=%.2f < %.2f)",
                            prob,
                            ML_PROBABILITY_THRESHOLD,
                        )
                        return None
                    if signal["action"] == "SELL" and prob > (
                        1 - ML_PROBABILITY_THRESHOLD
                    ):
                        logger.info(
                            "ML filter blocked SELL signal (prob=%.2f > %.2f)",
                            prob,
                            1 - ML_PROBABILITY_THRESHOLD,
                        )
                        return None
            except Exception as exc:
                logger.warning("ML prediction failed, passing signal through: %s", exc)

        signal["strategy"] = self._strategy_name
        logger.info(
            "Signal: %s %s @ %.2f — %s",
            signal["action"],
            signal["symbol"],
            signal["price"],
            signal["reason"],
        )

        if self._order_callback:
            self._order_callback(signal)

        return signal

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def status(self) -> dict:
        ctrl = self._controller.to_dict() if self._controller else {}
        return {
            "running": self.running,
            "state": ctrl.get("state", "IDLE"),
            "strategy": self._strategy_name,
            "use_ml": self.use_ml,
            "ticks_processed": self._tick_count,
            "halted_reason": self._halted_reason or ctrl.get("reason"),
        }
