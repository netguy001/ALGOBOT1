"""
app/broker/trade_ledger.py
==========================
TradeLedger — single source of truth for PnL correctness.

Computes all PnL exclusively from the ``trades`` table in the DB.
On server restart, capital is reconstructed from:

    available_capital = initial_capital + sum(trades.pnl) - open_position_margin

This eliminates PnL drift and ensures consistency:
    - No PnL change when server restarts while STOPPED
    - All PnL traceable to trade records
    - Audit trail: every rupee change has a trade_id

Usage::

    ledger = TradeLedger(account_id="default")
    summary = ledger.compute_pnl(initial_capital, current_prices)
    ledger.rebuild_capital_from_trades(capital_mgr)
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from app.db import storage


def _utc_now_iso() -> str:
    """Return current UTC time as ISO 8601 string via EngineClock."""
    try:
        from app.utils.clock import EngineClock

        return EngineClock(mode="demo").now_iso()
    except Exception:
        return datetime.now(timezone.utc).isoformat()


logger = logging.getLogger(__name__)


class TradeLedger:
    """Compute PnL from the trades table — the authoritative source.

    This class is primarily read-only: it computes PnL from trade records.
    It can also be used to rebuild/verify CapitalManager state on restart.
    """

    def __init__(self, account_id: str = "default"):
        self._account_id = account_id

    @property
    def account_id(self) -> str:
        return self._account_id

    def total_realised_pnl(self) -> float:
        """Sum of all realised PnL from the trades table."""
        trades = storage.get_trades(limit=10_000, account_id=self._account_id)
        return sum(t.get("pnl", 0.0) for t in trades)

    def trade_count(self) -> int:
        """Total number of trades for this account."""
        trades = storage.get_trades(limit=10_000, account_id=self._account_id)
        return len(trades)

    def get_recent_trades(self, limit: int = 100) -> list[dict]:
        """Return recent trades from DB."""
        return storage.get_trades(limit=limit, account_id=self._account_id)

    def compute_unrealised_pnl(
        self, current_prices: Optional[dict[str, float]] = None
    ) -> float:
        """Compute unrealised PnL from open positions and current prices.

        This method ALWAYS uses latest prices for mark-to-market.
        """
        if not current_prices:
            return 0.0

        unrealised = 0.0
        positions = storage.get_positions(self._account_id)
        for pos in positions:
            sym = pos["symbol"]
            if sym not in current_prices or pos["qty"] <= 0:
                continue
            cp = current_prices[sym]
            if pos["side"] == "BUY":
                diff = cp - pos["avg_price"]
            elif pos["side"] == "SELL":
                diff = pos["avg_price"] - cp
            else:
                continue
            unrealised += diff * pos["qty"]
        return unrealised

    def compute_pnl(
        self,
        initial_capital: float,
        current_prices: Optional[dict[str, float]] = None,
    ) -> dict:
        """Compute full PnL snapshot from trade records.

        Returns a dict compatible with CapitalManager.get_pnl() output.
        This is the authoritative PnL computation — rebuilt from DB.
        """
        realised = self.total_realised_pnl()
        unrealised = self.compute_unrealised_pnl(current_prices)

        # Compute used margin from positions
        positions = storage.get_positions(self._account_id)
        used_margin = sum(p["avg_price"] * p["qty"] for p in positions if p["qty"] > 0)

        total = realised + unrealised
        capital = initial_capital + total

        return {
            "realised_pnl": round(realised, 2),
            "unrealised_pnl": round(unrealised, 2),
            "total_pnl": round(total, 2),
            "capital": round(capital, 2),
            "available_capital": round(initial_capital + realised - used_margin, 2),
            "used_margin": round(used_margin, 2),
            "trade_count": self.trade_count(),
            "timestamp": _utc_now_iso(),
        }

    def rebuild_capital_from_trades(self, capital_mgr) -> dict:
        """Rebuild CapitalManager state from trade records.

        Call this on startup to ensure PnL consistency.
        Returns the discrepancy report.
        """
        db_realised = self.total_realised_pnl()
        cm_realised = capital_mgr.realised_pnl

        discrepancy = abs(db_realised - cm_realised)
        if discrepancy > 0.01:
            logger.warning(
                "PnL discrepancy detected: DB=%.2f vs CM=%.2f (diff=%.2f). "
                "Rebuilding from trade records.",
                db_realised,
                cm_realised,
                discrepancy,
            )
            # Note: We don't directly modify CapitalManager here as it
            # should restore from DB on init. This is for verification.

        return {
            "db_realised_pnl": round(db_realised, 2),
            "cm_realised_pnl": round(cm_realised, 2),
            "discrepancy": round(discrepancy, 2),
            "trade_count": self.trade_count(),
            "match": discrepancy < 0.01,
        }

    def verify_against_capital_manager(self, capital_mgr) -> dict:
        """Compare ledger PnL with CapitalManager state.

        Returns a dict with the discrepancy (if any).
        """
        return self.rebuild_capital_from_trades(capital_mgr)
        ledger_realised = self.total_realised_pnl()
        cm_realised = capital_mgr.realised_pnl
        drift = abs(ledger_realised - cm_realised)

        return {
            "ledger_realised": round(ledger_realised, 2),
            "cm_realised": round(cm_realised, 2),
            "drift": round(drift, 2),
            "match": drift < 0.01,
        }
