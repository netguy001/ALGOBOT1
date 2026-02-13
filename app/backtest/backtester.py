"""
app/backtest/backtester.py
==========================
Backtest engine that runs strategies over historical OHLCV data and
produces performance reports (Sharpe ratio, win rate, max drawdown,
equity curve).

Usage::

    python -m app.backtest.backtester
    python -m app.backtest.backtester --strategy rsi_mean_reversion --symbol TCS.NS

Reports are saved under ``app/backtest/reports/``.
"""

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from app.config import (
    DEFAULT_STRATEGY,
    INITIAL_CAPITAL,
    DEFAULT_STOP_LOSS_PCT,
    DEFAULT_TAKE_PROFIT_PCT,
)
from app.strategy.strategies import STRATEGY_REGISTRY
from app.utils.data import load_cached_ohlcv, download_ohlcv, resolve_symbol
from app.utils.risk import RiskParams, position_size, stop_loss_price, take_profit_price

logger = logging.getLogger(__name__)

REPORTS_DIR = Path(__file__).resolve().parent / "reports"


# ---------------------------------------------------------------------------
# Core backtest loop
# ---------------------------------------------------------------------------


def run_backtest(
    symbol: str,
    strategy_name: str = DEFAULT_STRATEGY,
    capital: float = INITIAL_CAPITAL,
    slippage_pct: float = 0.05,
    commission_pct: float = 0.03,
) -> dict:
    """
    Run a backtest and return performance metrics.

    Returns
    -------
    dict with keys:
        trades, equity_curve, sharpe, win_rate, max_drawdown, total_return
    """
    yf_sym = resolve_symbol(symbol)
    df = load_cached_ohlcv(yf_sym)
    if df.empty:
        df = download_ohlcv(yf_sym)
    if df.empty:
        logger.error("No data for %s", yf_sym)
        return {}

    # Instantiate strategy
    cls = STRATEGY_REGISTRY.get(strategy_name)
    if cls is None:
        logger.error("Unknown strategy: %s", strategy_name)
        return {}
    strat = cls()

    # State
    cash = capital
    position_qty = 0
    position_side = "FLAT"
    entry_price = 0.0
    trades: list[dict] = []
    equity_curve: list[float] = []
    peak = capital

    risk_params = RiskParams(capital=capital)

    for i, (date, row) in enumerate(df.iterrows()):
        price = float(row["Close"])
        tick = {
            "symbol": yf_sym,
            "price": price,
            "high": float(row.get("High", price)),
            "low": float(row.get("Low", price)),
            "volume": int(row.get("Volume", 0)),
            "timestamp": str(date),
        }

        # Check stop-loss / take-profit for open position
        if position_qty > 0:
            sl = stop_loss_price(entry_price, position_side)
            tp = take_profit_price(entry_price, position_side)

            hit_sl = (position_side == "BUY" and price <= sl) or (
                position_side == "SELL" and price >= sl
            )
            hit_tp = (position_side == "BUY" and price >= tp) or (
                position_side == "SELL" and price <= tp
            )

            if hit_sl or hit_tp:
                # Close position
                exit_price = (
                    price * (1 - slippage_pct / 100)
                    if position_side == "BUY"
                    else price * (1 + slippage_pct / 100)
                )
                pnl = (exit_price - entry_price) * position_qty
                if position_side == "SELL":
                    pnl = -pnl
                commission = exit_price * position_qty * (commission_pct / 100)
                pnl -= commission
                cash += pnl + (entry_price * position_qty)  # return capital + pnl
                trades.append(
                    {
                        "date": str(date),
                        "side": "CLOSE",
                        "price": round(exit_price, 2),
                        "qty": position_qty,
                        "pnl": round(pnl, 2),
                        "reason": "SL hit" if hit_sl else "TP hit",
                    }
                )
                position_qty = 0
                position_side = "FLAT"

        # Get strategy signal
        signal = strat.on_tick(tick)

        if signal and position_qty == 0:
            # Enter position
            risk_params.capital = cash
            qty = position_size(price, risk_params)
            cost = price * qty
            slip = cost * (slippage_pct / 100)
            commission = cost * (commission_pct / 100)
            total_cost = cost + slip + commission

            if total_cost <= cash:
                cash -= total_cost
                position_qty = qty
                position_side = signal["action"]
                entry_price = (
                    price * (1 + slippage_pct / 100)
                    if signal["action"] == "BUY"
                    else price * (1 - slippage_pct / 100)
                )
                trades.append(
                    {
                        "date": str(date),
                        "side": signal["action"],
                        "price": round(entry_price, 2),
                        "qty": qty,
                        "pnl": 0,
                        "reason": signal["reason"],
                    }
                )

        # Mark-to-market equity
        mtm = cash
        if position_qty > 0:
            mtm += price * position_qty
        equity_curve.append(mtm)

    # Close any remaining position at last price
    if position_qty > 0:
        last_price = float(df.iloc[-1]["Close"])
        pnl = (last_price - entry_price) * position_qty
        if position_side == "SELL":
            pnl = -pnl
        cash += pnl + entry_price * position_qty
        trades.append(
            {
                "date": str(df.index[-1]),
                "side": "CLOSE",
                "price": round(last_price, 2),
                "qty": position_qty,
                "pnl": round(pnl, 2),
                "reason": "End of backtest",
            }
        )

    # --- Compute metrics ---
    equity = np.array(equity_curve) if equity_curve else np.array([capital])
    returns = np.diff(equity) / equity[:-1] if len(equity) > 1 else np.array([0])

    # Sharpe ratio (annualised, assuming 252 trading days)
    sharpe = 0.0
    if len(returns) > 1 and np.std(returns) > 0:
        sharpe = (np.mean(returns) / np.std(returns)) * np.sqrt(252)

    # Win rate
    closed_trades = [t for t in trades if t["side"] == "CLOSE"]
    wins = sum(1 for t in closed_trades if t["pnl"] > 0)
    win_rate = (wins / len(closed_trades) * 100) if closed_trades else 0

    # Max drawdown
    max_dd = 0.0
    peak_val = equity[0]
    for val in equity:
        if val > peak_val:
            peak_val = val
        dd = (peak_val - val) / peak_val * 100
        if dd > max_dd:
            max_dd = dd

    total_return = ((equity[-1] - capital) / capital) * 100 if len(equity) > 0 else 0

    return {
        "symbol": yf_sym,
        "strategy": strategy_name,
        "initial_capital": capital,
        "final_capital": round(float(equity[-1]) if len(equity) > 0 else capital, 2),
        "total_return_pct": round(total_return, 2),
        "sharpe_ratio": round(sharpe, 4),
        "win_rate_pct": round(win_rate, 2),
        "max_drawdown_pct": round(max_dd, 2),
        "total_trades": len(trades),
        "trades": trades,
        "equity_curve": [round(e, 2) for e in equity_curve],
    }


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------


def save_report(result: dict) -> None:
    """Save JSON report and equity-curve chart."""
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    prefix = f"{result['symbol'].replace('.', '_')}_{result['strategy']}_{ts}"

    # JSON report
    json_path = REPORTS_DIR / f"{prefix}_report.json"
    with open(json_path, "w") as f:
        json.dump({k: v for k, v in result.items() if k != "equity_curve"}, f, indent=2)
    logger.info("Report saved to %s", json_path)

    # Equity curve plot
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        fig, ax = plt.subplots(figsize=(12, 5))
        ax.plot(result["equity_curve"], linewidth=1.2)
        ax.set_title(f"Equity Curve — {result['symbol']} / {result['strategy']}")
        ax.set_xlabel("Bar")
        ax.set_ylabel("Portfolio Value (₹)")
        ax.grid(True, alpha=0.3)

        # Annotate key metrics
        ax.annotate(
            f"Sharpe: {result['sharpe_ratio']:.2f}\n"
            f"Win Rate: {result['win_rate_pct']:.1f}%\n"
            f"Max DD: {result['max_drawdown_pct']:.1f}%\n"
            f"Return: {result['total_return_pct']:.1f}%",
            xy=(0.02, 0.95),
            xycoords="axes fraction",
            fontsize=9,
            verticalalignment="top",
            bbox=dict(boxstyle="round,pad=0.3", facecolor="wheat", alpha=0.5),
        )

        img_path = REPORTS_DIR / f"{prefix}_equity_curve.png"
        fig.savefig(img_path, dpi=100, bbox_inches="tight")
        plt.close(fig)
        logger.info("Equity curve saved to %s", img_path)
    except ImportError:
        logger.warning("matplotlib not available — skipping equity curve plot")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )

    parser = argparse.ArgumentParser(description="Run backtest")
    parser.add_argument("--symbol", default="RELIANCE.NS")
    parser.add_argument(
        "--strategy", default=DEFAULT_STRATEGY, choices=list(STRATEGY_REGISTRY.keys())
    )
    parser.add_argument("--capital", type=float, default=INITIAL_CAPITAL)
    args = parser.parse_args()

    result = run_backtest(
        symbol=args.symbol,
        strategy_name=args.strategy,
        capital=args.capital,
    )
    if result:
        save_report(result)
        print(f"\n{'='*50}")
        print(f"Symbol:       {result['symbol']}")
        print(f"Strategy:     {result['strategy']}")
        print(f"Initial:      ₹{result['initial_capital']:,.2f}")
        print(f"Final:        ₹{result['final_capital']:,.2f}")
        print(f"Return:       {result['total_return_pct']:.2f}%")
        print(f"Sharpe:       {result['sharpe_ratio']:.4f}")
        print(f"Win Rate:     {result['win_rate_pct']:.2f}%")
        print(f"Max Drawdown: {result['max_drawdown_pct']:.2f}%")
        print(f"Trades:       {result['total_trades']}")
        print(f"{'='*50}\n")
