"""
app/engine_controller.py
========================
Centralised engine state machine.

States
------
    IDLE     — system booted, no strategy active
    RUNNING  — strategy executing, orders flowing
    STOPPED  — user pressed stop; market data still streams but
               strategy, orders, PnL updates are all frozen
    PAUSED   — soft pause; can resume without a full restart

Every component that performs trading-related work must call::

    if not controller.is_running:
        return

This module is the **single source of truth** for "should we trade?".
"""

import enum
import logging
import threading
from typing import Optional

logger = logging.getLogger(__name__)


class EngineState(enum.Enum):
    IDLE = "IDLE"
    RUNNING = "RUNNING"
    STOPPED = "STOPPED"
    PAUSED = "PAUSED"


class EngineController:
    """Thread-safe centralised state machine for the trading engine."""

    def __init__(self, persist_fn=None, restore_fn=None) -> None:
        self._lock = threading.Lock()
        self._stop_event = threading.Event()  # signalled when STOPPED
        self._reason: Optional[str] = None
        self._persist_fn = persist_fn  # callable(state_str) -> None
        self._restore_fn = restore_fn  # callable() -> str | None

        # Restore state from DB if available
        self._state = EngineState.IDLE
        if restore_fn:
            try:
                saved = restore_fn()
                if saved and saved in ("RUNNING", "PAUSED"):
                    # Auto-resume to RUNNING if server was RUNNING before crash
                    self._state = EngineState.RUNNING
                    self._reason = "auto_resume"
                    logger.info(
                        "EngineController: auto-resumed from saved state %s", saved
                    )
                elif saved == "STOPPED":
                    self._state = EngineState.STOPPED
                    self._stop_event.set()
                    logger.info("EngineController: restored STOPPED state from DB")
            except Exception as exc:
                logger.warning("EngineController: failed to restore state: %s", exc)

    def _persist(self) -> None:
        """Persist current state to DB (if callback set)."""
        if self._persist_fn:
            try:
                self._persist_fn(self._state.value)
            except Exception as exc:
                logger.warning("EngineController: failed to persist state: %s", exc)

    # ── State queries ────────────────────────────────────────

    @property
    def state(self) -> EngineState:
        with self._lock:
            return self._state

    @property
    def is_running(self) -> bool:
        with self._lock:
            return self._state == EngineState.RUNNING

    @property
    def is_stopped(self) -> bool:
        with self._lock:
            return self._state == EngineState.STOPPED

    @property
    def stop_reason(self) -> Optional[str]:
        with self._lock:
            return self._reason

    @property
    def stop_event(self) -> threading.Event:
        """Threads can wait on this to know when the engine is stopped."""
        return self._stop_event

    # ── Transitions ──────────────────────────────────────────

    def start(self, reason: Optional[str] = None) -> bool:
        """Transition to RUNNING.  Returns True on success."""
        with self._lock:
            if self._state in (
                EngineState.IDLE,
                EngineState.STOPPED,
                EngineState.PAUSED,
            ):
                self._state = EngineState.RUNNING
                self._reason = reason
                self._stop_event.clear()
                logger.info("EngineController -> RUNNING  (%s)", reason or "user")
                self._persist()
                return True
            logger.warning("Cannot start: current state is %s", self._state.value)
            return False

    def stop(self, reason: Optional[str] = None) -> bool:
        """Transition to STOPPED.  Signals the stop_event."""
        with self._lock:
            if self._state in (EngineState.RUNNING, EngineState.PAUSED):
                self._state = EngineState.STOPPED
                self._reason = reason or "user_stop"
                self._stop_event.set()
                logger.info("EngineController -> STOPPED  (%s)", self._reason)
                self._persist()
                return True
            # Already stopped / idle — idempotent
            if self._state == EngineState.STOPPED:
                return True
            logger.warning("Cannot stop: current state is %s", self._state.value)
            return False

    def pause(self, reason: Optional[str] = None) -> bool:
        """Transition to PAUSED (soft pause — can resume)."""
        with self._lock:
            if self._state == EngineState.RUNNING:
                self._state = EngineState.PAUSED
                self._reason = reason or "user_pause"
                logger.info("EngineController -> PAUSED  (%s)", self._reason)
                self._persist()
                return True
            return False

    def reset(self) -> None:
        """Force back to IDLE (e.g. full system reset)."""
        with self._lock:
            self._state = EngineState.IDLE
            self._reason = None
            self._stop_event.clear()
            logger.info("EngineController -> IDLE (reset)")
            self._persist()

    def emergency_stop(self, reason: str) -> None:
        """Unconditional hard stop from any state."""
        with self._lock:
            self._state = EngineState.STOPPED
            self._reason = reason
            self._stop_event.set()
            self._persist()
        logger.warning("EMERGENCY STOP: %s", reason)

    # ── Serialisation ────────────────────────────────────────

    def to_dict(self) -> dict:
        with self._lock:
            return {
                "state": self._state.value,
                "running": self._state == EngineState.RUNNING,
                "reason": self._reason,
            }
