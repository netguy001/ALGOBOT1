"""
app/broker/order_validator.py
=============================
Pre-trade validation layer.

All checks run BEFORE an order is created. Rejection returns a human-readable
reason string; ``None`` means the order is approved.

Safety guards (defence-in-depth):
    1. Kill switch — hard stop, no exceptions
    2. Daily loss halt — automatic after threshold breach
    3. Duplicate signal idempotency
    4. Per-symbol tick-based cooldown
    5. Per-symbol TIME-based cooldown (prevents overtrading even with fast ticks)
    6. Position-aware: blocks doubling-down in same direction
    7. Max open positions
    8. Available capital check
    9. Total exposure cap
    10. Quantity sanity (will be clamped later, but reject if zero)
"""

import logging
import time as _time
from typing import Optional

logger = logging.getLogger(__name__)


class OrderValidator:
    """
    Validates a proposed trade against capital constraints, position limits,
    daily loss limits, and cooldown rules.
    """

    def __init__(
        self,
        capital_manager,
        cooldown_candles: int = 5,
        cooldown_seconds: float = 30.0,
        max_qty_per_order: int = 10_000,
    ):
        """
        Parameters
        ----------
        capital_manager : CapitalManager
            Single source of truth for capital / positions.
        cooldown_candles : int
            Minimum ticks between signals for the same symbol.
        cooldown_seconds : float
            Minimum wall-clock seconds between signals for the same symbol.
            Prevents overtrading even when ticks arrive rapidly.
        max_qty_per_order : int
            Absolute per-order share limit (sanity check).
        """
        self._cm = capital_manager
        self._cooldown_candles = cooldown_candles
        self._cooldown_seconds = cooldown_seconds
        self._max_qty = max_qty_per_order

        # Per-symbol cooldown tracker: symbol -> last_signal_tick
        self._last_signal_tick: dict[str, int] = {}
        # Per-symbol TIME cooldown: symbol -> last_signal_epoch
        self._last_signal_time: dict[str, float] = {}
        # Idempotency set
        self._recent_signals: set[str] = set()
        self._tick_counter: int = 0

    def tick(self) -> None:
        """Advance the internal tick counter (called once per tick cycle)."""
        self._tick_counter += 1

    # ------------------------------------------------------------------
    # Main validation entry point
    # ------------------------------------------------------------------

    def validate_signal(self, signal: dict) -> Optional[str]:
        """
        Validate a strategy signal before order creation.

        Returns
        -------
        None
            Signal is approved — proceed with order.
        str
            Rejection reason.
        """
        sym = signal.get("symbol", "")
        action = signal.get("action", "")
        price = signal.get("price", 0.0)

        # 1. Kill switch — absolute hard stop
        if getattr(self._cm, "kill_switch", False):
            return "kill_switch_active"

        # 2. Daily loss halt
        if self._cm.daily_loss_halted:
            return "daily_loss_halted"

        if self._cm.check_daily_loss():
            return "daily_loss_limit_breached"

        # 3. Idempotency — exact duplicate signal
        sig_key = f"{sym}_{action}_{price}"
        if sig_key in self._recent_signals:
            return "duplicate_signal"
        self._recent_signals.add(sig_key)
        if len(self._recent_signals) > 500:
            self._recent_signals.clear()

        # 4. Per-symbol TICK cooldown
        last_tick = self._last_signal_tick.get(sym, -self._cooldown_candles - 1)
        if self._tick_counter - last_tick < self._cooldown_candles:
            return f"cooldown_active ({self._tick_counter - last_tick}/{self._cooldown_candles} ticks)"

        # 5. Per-symbol TIME cooldown — prevents overtrading even with rapid ticks
        now = _time.time()
        last_time = self._last_signal_time.get(sym, 0.0)
        elapsed = now - last_time
        if elapsed < self._cooldown_seconds:
            return f"time_cooldown_active ({elapsed:.1f}s / {self._cooldown_seconds}s)"

        # 6. Position-aware: block same-direction if already exposed
        pos = self._cm.get_position(sym)
        if pos["qty"] > 0 and pos["side"] == action:
            return f"already_{action.lower()}_{sym}"

        # 7. Max open positions
        if (
            pos["qty"] == 0
            and self._cm.open_position_count >= self._cm.max_open_positions
        ):
            return f"max_open_positions ({self._cm.max_open_positions})"

        # 8. Available capital
        if self._cm.available_capital <= 0:
            return "no_available_capital"

        # 9. Total exposure cap
        if self._cm.total_exposure >= self._cm.max_exposure_pct:
            return f"max_exposure ({self._cm.total_exposure:.1f}% >= {self._cm.max_exposure_pct}%)"

        # 10. Quantity sanity (will be clamped later, but reject if zero)
        clamped = self._cm.clamp_quantity(self._max_qty, price)
        if clamped <= 0:
            return "clamped_quantity_zero"

        return None  # approved

    def validate_manual_order(
        self, symbol: str, side: str, qty: int, price: float
    ) -> Optional[str]:
        """
        Validate a manually placed order (from the UI).

        Lighter checks than strategy signals — no cooldown, no idempotency.
        Kill switch still applies to manual orders.
        """
        if getattr(self._cm, "kill_switch", False):
            return "kill_switch_active"

        if self._cm.daily_loss_halted:
            return "daily_loss_halted"

        if qty <= 0:
            return "invalid_quantity"

        if qty > self._max_qty:
            return f"qty_exceeds_limit ({qty} > {self._max_qty})"

        if price < 0:
            return "invalid_price"

        if self._cm.available_capital <= 0:
            return "no_available_capital"

        return None

    # ------------------------------------------------------------------
    # Cooldown management
    # ------------------------------------------------------------------

    def record_signal(self, symbol: str) -> None:
        """Record that a signal was acted on for cooldown tracking."""
        self._last_signal_tick[symbol] = self._tick_counter
        self._last_signal_time[symbol] = _time.time()

    def reset_cooldowns(self) -> None:
        """Reset all cooldown state (e.g. on strategy switch)."""
        self._last_signal_tick.clear()
        self._last_signal_time.clear()
        self._recent_signals.clear()
