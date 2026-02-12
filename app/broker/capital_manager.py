"""
app/broker/capital_manager.py
=============================
Centralised capital and position management.

Single source of truth for:
    - available_capital (what can be deployed in new trades)
    - used_margin       (locked in open positions)
    - realised_pnl      (closed trade P&L)
    - unrealised_pnl    (mark-to-market on open positions)

Hard caps enforced:
    - MAX_POSITION_SIZE_PER_TRADE   per-trade notional limit
    - MAX_OPEN_POSITIONS            concurrent open symbols
    - MAX_TOTAL_EXPOSURE_PERCENT    portfolio-level exposure cap
    - MAX_QTY_PER_ORDER             absolute share count sanity limit

Capital flow:
    - On order fill (opening): available_capital -= qty * fill_price
    - On position close:       available_capital += margin + realised_pnl
    - NEVER allow qty * price > available_capital  (enforced in clamp_quantity)
"""

import logging
import threading
from typing import Any, Optional

logger = logging.getLogger(__name__)


class CapitalManager:
    """
    Thread-safe capital and position tracker.

    Instantiated once per session. OrderManager delegates all capital
    and position queries here.
    """

    def __init__(
        self,
        initial_capital: float,
        max_position_size: int = 500,
        max_open_positions: int = 10,
        max_exposure_pct: float = 80.0,
        max_qty_per_order: int = 10_000,
        daily_loss_limit: float = 50_000.0,
    ):
        self._initial_capital = initial_capital
        self._available_capital: float = initial_capital  # Tracks free cash explicitly
        self._realised_pnl: float = 0.0
        self._positions: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

        # Hard caps
        self.max_position_size = max_position_size
        self.max_open_positions = max_open_positions
        self.max_exposure_pct = max_exposure_pct
        self.max_qty_per_order = max_qty_per_order
        self.daily_loss_limit = daily_loss_limit

        # Kill-switch state
        self._daily_loss_halted: bool = False
        self._kill_switch: bool = False

    # ------------------------------------------------------------------
    # Read-only properties
    # ------------------------------------------------------------------

    @property
    def initial_capital(self) -> float:
        return self._initial_capital

    @property
    def realised_pnl(self) -> float:
        return self._realised_pnl

    @property
    def daily_loss_halted(self) -> bool:
        return self._daily_loss_halted

    @property
    def kill_switch(self) -> bool:
        return self._kill_switch

    @property
    def used_margin(self) -> float:
        """Sum of notional value locked in all open positions."""
        with self._lock:
            return sum(
                p["avg_price"] * p["qty"]
                for p in self._positions.values()
                if p.get("qty", 0) > 0
            )

    @property
    def available_capital(self) -> float:
        """Capital deployable for new trades.

        Tracked explicitly: reduced on each fill, restored (margin + pnl)
        on each position close. Unrealised PnL is deliberately excluded
        to prevent infinite compounding off paper gains.
        """
        return max(0.0, self._available_capital)

    @property
    def open_position_count(self) -> int:
        with self._lock:
            return sum(1 for p in self._positions.values() if p.get("qty", 0) > 0)

    @property
    def total_exposure(self) -> float:
        """Total exposure as a percentage of initial capital + realised_pnl."""
        base = self._initial_capital + self._realised_pnl
        if base <= 0:
            return 100.0
        return (self.used_margin / base) * 100.0

    # ------------------------------------------------------------------
    # Capital queries
    # ------------------------------------------------------------------

    def unrealised_pnl(self, current_prices: dict[str, float]) -> float:
        """Compute mark-to-market unrealised P&L across all positions.

        For LONG:  (current_price - avg_entry_price) * qty
        For SHORT: (avg_entry_price - current_price) * qty

        Never multiply by total capital — only by qty. This prevents
        the PnL spike bug where unrealised PnL was inflated.
        """
        total = 0.0
        with self._lock:
            for sym, pos in self._positions.items():
                if pos["qty"] <= 0 or sym not in current_prices:
                    continue
                if pos["side"] == "BUY":
                    # Long position: profit when price goes up
                    diff = current_prices[sym] - pos["avg_price"]
                elif pos["side"] == "SELL":
                    # Short position: profit when price goes down
                    diff = pos["avg_price"] - current_prices[sym]
                else:
                    continue
                total += diff * pos["qty"]
        return total

    def get_pnl(self, current_prices: Optional[dict[str, float]] = None) -> dict:
        """Full P&L snapshot for the UI."""
        unreal = self.unrealised_pnl(current_prices) if current_prices else 0.0
        return {
            "realised_pnl": round(self._realised_pnl, 2),
            "unrealised_pnl": round(unreal, 2),
            "total_pnl": round(self._realised_pnl + unreal, 2),
            "capital": round(self._initial_capital + self._realised_pnl + unreal, 2),
            "available_capital": round(self.available_capital, 2),
            "used_margin": round(self.used_margin, 2),
            "daily_loss_halted": self._daily_loss_halted,
            "kill_switch": self._kill_switch,
            "trade_count": 0,  # filled by caller
        }

    # ------------------------------------------------------------------
    # Position management
    # ------------------------------------------------------------------

    def get_positions(self) -> dict[str, dict]:
        with self._lock:
            return {sym: dict(pos) for sym, pos in self._positions.items()}

    def get_position(self, symbol: str) -> dict:
        """Return position for a single symbol (or FLAT stub)."""
        with self._lock:
            return dict(
                self._positions.get(
                    symbol, {"qty": 0, "avg_price": 0.0, "side": "FLAT"}
                )
            )

    def update_position(
        self, symbol: str, side: str, fill_qty: int, fill_price: float
    ) -> float:
        """
        Apply a fill to the position book.

        Capital flow:
            - Opening/adding: reduce available_capital by fill notional
            - Closing/reducing: restore margin + pnl to available_capital

        Returns the realised PnL generated by this fill (0 if adding to position).
        """
        pnl = 0.0
        with self._lock:
            pos = self._positions.get(
                symbol, {"qty": 0, "avg_price": 0.0, "side": "FLAT"}
            )

            if pos["side"] == "FLAT" or pos["qty"] == 0:
                # Opening new position — lock margin
                margin = fill_qty * fill_price
                self._available_capital -= margin
                pos = {"qty": fill_qty, "avg_price": fill_price, "side": side}

            elif pos["side"] == side:
                # Adding to existing position — lock additional margin
                additional_margin = fill_qty * fill_price
                self._available_capital -= additional_margin
                total_qty = pos["qty"] + fill_qty
                pos["avg_price"] = (
                    (pos["avg_price"] * pos["qty"]) + (fill_price * fill_qty)
                ) / total_qty
                pos["qty"] = total_qty

            else:
                # Reducing / closing / reversing position
                if fill_qty >= pos["qty"]:
                    # Close entire position (and maybe reverse)
                    closed_qty = pos["qty"]
                    # PnL on closed portion
                    if pos["side"] == "BUY":
                        pnl = (fill_price - pos["avg_price"]) * closed_qty
                    else:  # SELL (short)
                        pnl = (pos["avg_price"] - fill_price) * closed_qty

                    # Restore margin + pnl to available capital
                    released_margin = pos["avg_price"] * closed_qty
                    self._available_capital += released_margin + pnl

                    remaining = fill_qty - closed_qty
                    if remaining > 0:
                        # Reversal: open new position in opposite direction
                        new_margin = remaining * fill_price
                        self._available_capital -= new_margin
                        pos = {"qty": remaining, "avg_price": fill_price, "side": side}
                    else:
                        pos = {"qty": 0, "avg_price": 0.0, "side": "FLAT"}
                else:
                    # Partial close
                    if pos["side"] == "BUY":
                        pnl = (fill_price - pos["avg_price"]) * fill_qty
                    else:
                        pnl = (pos["avg_price"] - fill_price) * fill_qty

                    # Restore partial margin + pnl
                    released_margin = pos["avg_price"] * fill_qty
                    self._available_capital += released_margin + pnl
                    pos["qty"] -= fill_qty

            self._positions[symbol] = pos
            self._realised_pnl += pnl

        return pnl

    # ------------------------------------------------------------------
    # Quantity clamping
    # ------------------------------------------------------------------

    def clamp_quantity(self, qty: int, price: float) -> int:
        """
        Enforce hard caps on order quantity.

        Caps applied:
            1. MAX_QTY_PER_ORDER (absolute sanity)
            2. MAX_POSITION_SIZE_PER_TRADE (share count cap)
            3. Cannot exceed available_capital — NEVER allow qty * price > available_capital
            4. Cannot breach MAX_TOTAL_EXPOSURE_PERCENT
        """
        # 1. Absolute qty cap
        qty = min(qty, self.max_qty_per_order)

        if price <= 0:
            return max(qty, 1)

        # 2. Per-trade share cap
        qty = min(qty, self.max_position_size)

        # 3. Available capital cap — the critical guard
        avail = self.available_capital
        if avail > 0 and price > 0:
            max_by_capital = int(avail / price)
            qty = min(qty, max_by_capital)
        else:
            qty = 0

        # 4. Total exposure cap
        base = self._initial_capital + self._realised_pnl
        if base > 0:
            max_exposure_value = base * (self.max_exposure_pct / 100.0)
            headroom = max_exposure_value - self.used_margin
            if headroom > 0:
                max_by_exposure = int(headroom / price)
                qty = min(qty, max_by_exposure)
            else:
                qty = 0

        return max(qty, 0)

    # ------------------------------------------------------------------
    # Kill switch and daily loss
    # ------------------------------------------------------------------

    def check_daily_loss(self) -> bool:
        """
        Check if daily loss limit is breached.
        Returns True if halted (either now or previously).
        """
        if self._daily_loss_halted:
            return True
        if self._realised_pnl <= -self.daily_loss_limit:
            self._daily_loss_halted = True
            logger.warning(
                "DAILY LOSS LIMIT breached (realised=%.2f, limit=%.2f). Trading halted.",
                self._realised_pnl,
                self.daily_loss_limit,
            )
            return True
        return False

    def halt(self) -> None:
        """Manual kill switch — stops all new order creation."""
        self._kill_switch = True
        self._daily_loss_halted = True
        logger.warning("CapitalManager: KILL SWITCH activated. All trading halted.")

    def reset_halt(self) -> None:
        """Reset the halt flag (e.g. for a new trading day)."""
        self._daily_loss_halted = False
        self._kill_switch = False
