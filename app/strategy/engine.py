"""
app/strategy/engine.py
======================
Strategy manager — loads strategies, feeds ticks, and routes resulting
trade signals to the order manager.

Designed to run in a background thread controlled by start() / stop().
"""

import logging
import threading
from typing import Optional

from app.strategy.strategies import STRATEGY_REGISTRY, Signal
from app.config import DEFAULT_STRATEGY, ML_ENABLED, ML_PROBABILITY_THRESHOLD

logger = logging.getLogger(__name__)


class StrategyEngine:
    """
    Orchestrates strategy execution.

    Usage::

        engine = StrategyEngine(order_callback=send_order)
        engine.set_strategy("sma_crossover")
        engine.on_tick(tick_dict)  # call repeatedly with each tick
    """

    def __init__(
        self,
        order_callback=None,
        ml_predict_fn=None,
        use_ml: bool = ML_ENABLED,
    ):
        """
        Parameters
        ----------
        order_callback : callable(signal) -> None
            Function invoked when a strategy emits a signal.
        ml_predict_fn : callable(symbol, price) -> float | None
            Returns probability of up-move (0-1). ``None`` if model unavailable.
        use_ml : bool
            Whether to filter signals through the ML model.
        """
        self._strategy = None
        self._strategy_name: str = DEFAULT_STRATEGY
        self._running = False
        self._lock = threading.Lock()
        self._order_callback = order_callback
        self._ml_predict_fn = ml_predict_fn
        self.use_ml = use_ml
        self._tick_count = 0

        self.set_strategy(self._strategy_name)

    # ------------------------------------------------------------------
    # Strategy selection
    # ------------------------------------------------------------------

    def set_strategy(self, name: str, **kwargs) -> None:
        """Switch the active strategy by name."""
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
        return self._running

    # ------------------------------------------------------------------
    # Control
    # ------------------------------------------------------------------

    def start(self) -> None:
        self._running = True
        logger.info("Strategy engine started (%s)", self._strategy_name)

    def stop(self) -> None:
        self._running = False
        logger.info("Strategy engine stopped")

    def reset(self) -> None:
        """Reset the current strategy's internal state."""
        with self._lock:
            if self._strategy:
                self._strategy.reset()
            self._tick_count = 0
        logger.info("Strategy engine reset")

    # ------------------------------------------------------------------
    # Tick processing
    # ------------------------------------------------------------------

    def on_tick(self, tick: dict) -> Optional[Signal]:
        """
        Feed a tick into the active strategy.

        Returns the signal (if any) for callers that need it,
        and also invokes the ``order_callback`` if set.
        """
        if not self._running or self._strategy is None:
            return None

        self._tick_count += 1

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
        return {
            "running": self._running,
            "strategy": self._strategy_name,
            "use_ml": self.use_ml,
            "ticks_processed": self._tick_count,
        }
