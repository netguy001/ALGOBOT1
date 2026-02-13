"""
app/broker/capital_manager.py
=============================
DB-backed capital and position management.

**Single source of truth** for:
    - available_capital (free cash for new trades)
    - used_margin       (locked in open positions)
    - realised_pnl      (closed trade P&L)
    - unrealised_pnl    (mark-to-market on open positions)

Architecture:
    - On init: loads account + positions from SQLite DB
    - On every fill: persists position + capital to DB immediately
    - In-memory cache mirrors DB for fast reads
    - On restart: state is fully restored from DB (no PnL reset!)

Hard caps enforced:
    - MAX_POSITION_SIZE_PER_TRADE   per-trade notional limit
    - MAX_OPEN_POSITIONS            concurrent open symbols
    - MAX_TOTAL_EXPOSURE_PERCENT    portfolio-level exposure cap
    - MAX_QTY_PER_ORDER             absolute share count sanity limit

Capital flow:
    - On order fill (opening): available_capital -= qty * fill_price
    - On position close:       available_capital += margin + realised_pnl
    - NEVER allow qty * price > available_capital
"""

import logging
import threading
from typing import Any, Optional

from app.db import storage

logger = logging.getLogger(__name__)


class CapitalManager:
    """
    Thread-safe, DB-backed capital and position tracker.

    Instantiated once per session. On init, state is restored from DB.
    Every mutation persists immediately, so a restart never loses PnL.
    """

    def __init__(
        self,
        initial_capital: float,
        max_position_size: int = 500,
        max_open_positions: int = 10,
        max_exposure_pct: float = 80.0,
        max_qty_per_order: int = 10_000,
        daily_loss_limit: float = 50_000.0,
        account_id: str = "default",
    ):
        self._account_id = account_id
        self._initial_capital = initial_capital
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

        # ── Restore state from DB ──────────────────────────────
        acct = storage.ensure_default_account(initial_capital, account_id)
        self._available_capital: float = acct["available_capital"]
        self._realised_pnl: float = acct["realised_pnl"]

        # Restore daily_loss_halted from DB (survives restart)
        try:
            self._daily_loss_halted = bool(acct.get("daily_loss_halted", 0))
        except (KeyError, TypeError):
            self._daily_loss_halted = False

        # Load positions from DB into in-memory cache
        self._positions: dict[str, dict[str, Any]] = {}
        db_positions = storage.get_positions(account_id)
        for p in db_positions:
            self._positions[p["symbol"]] = {
                "qty": p["qty"],
                "avg_price": p["avg_price"],
                "side": p["side"],
            }

        logger.info(
            "CapitalManager restored from DB: account=%s  capital=%.2f  "
            "realised_pnl=%.2f  positions=%d",
            account_id,
            self._available_capital,
            self._realised_pnl,
            len(self._positions),
        )

    # ------------------------------------------------------------------
    # Read-only properties
    # ------------------------------------------------------------------

    @property
    def account_id(self) -> str:
        return self._account_id

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
    # DB persistence (called inside _lock)
    # ------------------------------------------------------------------

    def _persist_account(self) -> None:
        """Write current capital + realised PnL to DB."""
        try:
            storage.update_account(
                self._account_id,
                self._available_capital,
                self._realised_pnl,
            )
        except Exception as exc:
            logger.error("Failed to persist account state: %s", exc)

    def _persist_position(self, symbol: str) -> None:
        """Write a single position to DB."""
        try:
            pos = self._positions.get(symbol)
            if pos is None or pos["qty"] <= 0:
                storage.upsert_position(symbol, "FLAT", 0, 0.0, self._account_id)
            else:
                storage.upsert_position(
                    symbol,
                    pos["side"],
                    pos["qty"],
                    pos["avg_price"],
                    self._account_id,
                )
        except Exception as exc:
            logger.error("Failed to persist position %s: %s", symbol, exc)

    # ------------------------------------------------------------------
    # Capital queries
    # ------------------------------------------------------------------

    def unrealised_pnl(self, current_prices: dict[str, float]) -> float:
        """Compute mark-to-market unrealised P&L across all positions.

        For LONG:  (current_price - avg_entry_price) * qty
        For SHORT: (avg_entry_price - current_price) * qty
        """
        total = 0.0
        with self._lock:
            for sym, pos in self._positions.items():
                if pos["qty"] <= 0 or sym not in current_prices:
                    continue
                if pos["side"] == "BUY":
                    diff = current_prices[sym] - pos["avg_price"]
                elif pos["side"] == "SELL":
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
        Apply a fill to the position book and persist to DB.

        Capital flow:
            - Opening/adding: reduce available_capital by fill notional
            - Closing/reducing: restore margin + pnl to available_capital

        Returns the realised PnL generated by this fill (0 if adding).
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
                    if pos["side"] == "BUY":
                        pnl = (fill_price - pos["avg_price"]) * closed_qty
                    else:
                        pnl = (pos["avg_price"] - fill_price) * closed_qty

                    released_margin = pos["avg_price"] * closed_qty
                    self._available_capital += released_margin + pnl

                    remaining = fill_qty - closed_qty
                    if remaining > 0:
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

                    released_margin = pos["avg_price"] * fill_qty
                    self._available_capital += released_margin + pnl
                    pos["qty"] -= fill_qty

            self._positions[symbol] = pos
            self._realised_pnl += pnl

            # ── PERSIST TO DB (inside lock for consistency) ──
            self._persist_position(symbol)
            self._persist_account()

        logger.debug(
            "Position update: %s %s %d@%.2f → pnl=%.2f  avail=%.2f",
            side,
            symbol,
            fill_qty,
            fill_price,
            pnl,
            self._available_capital,
        )
        return pnl

    # ------------------------------------------------------------------
    # Risk-Based Position Sizing
    # ------------------------------------------------------------------

    def compute_position_size(
        self,
        price: float,
        stop_loss_price: float = 0.0,
        risk_per_trade_pct: float = 1.0,
        method: str = "fixed",
    ) -> int:
        """
        Compute position size based on risk management rules.

        Methods:
            - "fixed": Fixed percentage of current equity
            - "atr": Risk % divided by stop distance (requires stop_loss_price)

        Args:
            price: Entry price per share
            stop_loss_price: Stop loss price (required for ATR method)
            risk_per_trade_pct: Percentage of equity to risk (default 1%)
            method: Sizing method ("fixed" or "atr")

        Returns:
            int: Recommended position size (clamped by all caps)
        """
        if price <= 0:
            return 0

        # Current equity = initial_capital + realised_pnl (conservative: no unrealised)
        equity = max(0.0, self._initial_capital + self._realised_pnl)
        if equity <= 0:
            return 0

        risk_amount = equity * (risk_per_trade_pct / 100.0)

        if method == "atr" and stop_loss_price > 0:
            # ATR-style: position size = risk_amount / risk_per_share
            risk_per_share = abs(price - stop_loss_price)
            if risk_per_share <= 0:
                risk_per_share = price * 0.02  # fallback: 2% of price
            qty = int(risk_amount / risk_per_share)
        else:
            # Fixed method: position size = risk_amount / price
            qty = int(risk_amount / price)

        # Apply all hard caps via clamp
        return self.clamp_quantity(qty, price)

    def get_equity(self, current_prices: dict[str, float] = None) -> float:
        """
        Get current account equity (capital + realised + unrealised).

        Args:
            current_prices: Dict of symbol -> current price for mark-to-market

        Returns:
            Total equity value
        """
        base = self._initial_capital + self._realised_pnl
        if current_prices:
            base += self.unrealised_pnl(current_prices)
        return max(0.0, base)

    def get_drawdown(self, current_prices: dict[str, float] = None) -> dict:
        """
        Compute current drawdown from initial capital.

        Returns:
            dict with:
                - current_equity: Current equity value
                - peak_equity: Initial capital (conservative peak)
                - drawdown_pct: Percentage drawdown from peak
                - drawdown_value: Absolute drawdown value
        """
        current = self.get_equity(current_prices)
        peak = self._initial_capital  # Conservative: use starting capital as peak
        if peak <= 0:
            peak = current

        drawdown_value = max(0.0, peak - current)
        drawdown_pct = (drawdown_value / peak * 100) if peak > 0 else 0.0

        return {
            "current_equity": round(current, 2),
            "peak_equity": round(peak, 2),
            "drawdown_value": round(drawdown_value, 2),
            "drawdown_pct": round(drawdown_pct, 2),
        }

    # ------------------------------------------------------------------
    # Quantity clamping
    # ------------------------------------------------------------------

    def clamp_quantity(self, qty: int, price: float) -> int:
        """
        Enforce hard caps on order quantity.

        Caps applied:
            1. MAX_QTY_PER_ORDER (absolute sanity)
            2. MAX_POSITION_SIZE_PER_TRADE (share count cap)
            3. Cannot exceed available_capital
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
        """Check if daily loss limit is breached.

        Returns True if halted (either now or previously).
        """
        if self._daily_loss_halted:
            return True
        if self._realised_pnl <= -self.daily_loss_limit:
            self._daily_loss_halted = True
            try:
                storage.update_daily_loss_halted(self._account_id, True)
            except Exception:
                pass
            logger.warning(
                "DAILY LOSS LIMIT breached (realised=%.2f, limit=%.2f). "
                "Trading halted.",
                self._realised_pnl,
                self.daily_loss_limit,
            )
            return True
        return False

    def halt(self) -> None:
        """Manual kill switch — stops all new order creation."""
        self._kill_switch = True
        self._daily_loss_halted = True
        try:
            storage.update_daily_loss_halted(self._account_id, True)
        except Exception:
            pass
        logger.warning("CapitalManager: KILL SWITCH activated.")

    def reset_halt(self) -> None:
        """Reset the halt flag (e.g. for a new trading day)."""
        self._daily_loss_halted = False
        self._kill_switch = False
        try:
            storage.update_daily_loss_halted(self._account_id, False)
        except Exception:
            pass
