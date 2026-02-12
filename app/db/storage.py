"""
app/db/storage.py
=================
SQLite-backed persistence for orders, trades, PnL snapshots, and strategy logs.
Thread-safe: uses `check_same_thread=False` and a module-level lock for writes.
"""

import sqlite3
import threading
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from app.config import DB_PATH

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_conn: Optional[sqlite3.Connection] = None


# ---------------------------------------------------------------------------
# Initialisation
# ---------------------------------------------------------------------------


def _get_conn() -> sqlite3.Connection:
    """Return (and cache) a module-level SQLite connection."""
    global _conn
    if _conn is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL;")
        _init_tables(_conn)
    return _conn


def _init_tables(conn: sqlite3.Connection) -> None:
    """Create tables if they don't already exist."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS orders (
            order_id   TEXT PRIMARY KEY,
            symbol     TEXT NOT NULL,
            side       TEXT NOT NULL,          -- BUY / SELL
            qty        INTEGER NOT NULL,
            price      REAL,
            order_type TEXT DEFAULT 'MARKET',  -- MARKET / LIMIT
            status     TEXT DEFAULT 'NEW',     -- NEW/ACK/PARTIAL/FILLED/CANCELLED/REJECTED
            filled_qty INTEGER DEFAULT 0,
            avg_price  REAL DEFAULT 0,
            strategy   TEXT,
            created_at TEXT,
            updated_at TEXT
        );

        CREATE TABLE IF NOT EXISTS trades (
            trade_id   INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id   TEXT,
            symbol     TEXT,
            side       TEXT,
            qty        INTEGER,
            price      REAL,
            timestamp  TEXT,
            FOREIGN KEY (order_id) REFERENCES orders(order_id)
        );

        CREATE TABLE IF NOT EXISTS pnl_history (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp     TEXT,
            realised_pnl  REAL,
            unrealised_pnl REAL,
            total_pnl     REAL,
            capital       REAL
        );

        CREATE TABLE IF NOT EXISTS strategy_logs (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp  TEXT,
            strategy   TEXT,
            symbol     TEXT,
            signal     TEXT,
            details    TEXT
        );

        CREATE TABLE IF NOT EXISTS candles (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol     TEXT NOT NULL,
            timeframe  TEXT NOT NULL DEFAULT '1m',
            timestamp  INTEGER NOT NULL,
            open       REAL NOT NULL,
            high       REAL NOT NULL,
            low        REAL NOT NULL,
            close      REAL NOT NULL,
            volume     REAL NOT NULL DEFAULT 0,
            UNIQUE(symbol, timeframe, timestamp)
        );

        CREATE INDEX IF NOT EXISTS idx_candles_sym_tf
            ON candles(symbol, timeframe, timestamp DESC);
        """
    )
    conn.commit()
    logger.info("Database tables initialised at %s", DB_PATH)


# ---------------------------------------------------------------------------
# Order helpers
# ---------------------------------------------------------------------------


def insert_order(order: dict[str, Any]) -> None:
    """Insert a new order row."""
    with _lock:
        conn = _get_conn()
        conn.execute(
            """INSERT INTO orders
               (order_id, symbol, side, qty, price, order_type, status,
                filled_qty, avg_price, strategy, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                order["order_id"],
                order["symbol"],
                order["side"],
                order["qty"],
                order.get("price"),
                order.get("order_type", "MARKET"),
                order.get("status", "NEW"),
                order.get("filled_qty", 0),
                order.get("avg_price", 0),
                order.get("strategy"),
                order.get("created_at", datetime.utcnow().isoformat()),
                order.get("updated_at", datetime.utcnow().isoformat()),
            ),
        )
        conn.commit()


def update_order(order_id: str, updates: dict[str, Any]) -> None:
    """Update order fields by order_id."""
    with _lock:
        conn = _get_conn()
        updates["updated_at"] = datetime.utcnow().isoformat()
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [order_id]
        conn.execute(f"UPDATE orders SET {set_clause} WHERE order_id = ?", values)
        conn.commit()


def get_order(order_id: str) -> Optional[dict]:
    """Return a single order dict or None."""
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM orders WHERE order_id = ?", (order_id,)
    ).fetchone()
    return dict(row) if row else None


def get_all_orders(limit: int = 100, offset: int = 0) -> list[dict]:
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM orders ORDER BY created_at DESC LIMIT ? OFFSET ?",
        (limit, offset),
    ).fetchall()
    return [dict(r) for r in rows]


def get_open_orders() -> list[dict]:
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM orders WHERE status IN ('NEW','ACK','PARTIAL') ORDER BY created_at DESC"
    ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Trade helpers
# ---------------------------------------------------------------------------


def insert_trade(trade: dict[str, Any]) -> None:
    with _lock:
        conn = _get_conn()
        conn.execute(
            """INSERT INTO trades (order_id, symbol, side, qty, price, timestamp)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                trade["order_id"],
                trade["symbol"],
                trade["side"],
                trade["qty"],
                trade["price"],
                trade.get("timestamp", datetime.utcnow().isoformat()),
            ),
        )
        conn.commit()


def insert_order_and_trade(order: dict[str, Any], trade: dict[str, Any]) -> None:
    """Insert/update an order and its trade fill in a single transaction."""
    with _lock:
        conn = _get_conn()
        try:
            conn.execute("BEGIN")
            updates = {
                "status": order.get("status", "FILLED"),
                "filled_qty": order.get("filled_qty", 0),
                "avg_price": order.get("avg_price", 0),
                "updated_at": datetime.utcnow().isoformat(),
            }
            set_clause = ", ".join(f"{k} = ?" for k in updates)
            values = list(updates.values()) + [order["order_id"]]
            conn.execute(f"UPDATE orders SET {set_clause} WHERE order_id = ?", values)
            conn.execute(
                """INSERT INTO trades (order_id, symbol, side, qty, price, timestamp)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    trade["order_id"],
                    trade["symbol"],
                    trade["side"],
                    trade["qty"],
                    trade["price"],
                    trade.get("timestamp", datetime.utcnow().isoformat()),
                ),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def get_trades(limit: int = 200) -> list[dict]:
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM trades ORDER BY timestamp DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# PnL history
# ---------------------------------------------------------------------------


def insert_pnl_snapshot(snapshot: dict[str, Any]) -> None:
    with _lock:
        conn = _get_conn()
        conn.execute(
            """INSERT INTO pnl_history (timestamp, realised_pnl, unrealised_pnl, total_pnl, capital)
               VALUES (?, ?, ?, ?, ?)""",
            (
                snapshot.get("timestamp", datetime.utcnow().isoformat()),
                snapshot.get("realised_pnl", 0),
                snapshot.get("unrealised_pnl", 0),
                snapshot.get("total_pnl", 0),
                snapshot.get("capital", 0),
            ),
        )
        conn.commit()


def get_pnl_history(limit: int = 500) -> list[dict]:
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM pnl_history ORDER BY timestamp DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Strategy logs
# ---------------------------------------------------------------------------


def insert_strategy_log(log: dict[str, Any]) -> None:
    with _lock:
        conn = _get_conn()
        conn.execute(
            """INSERT INTO strategy_logs (timestamp, strategy, symbol, signal, details)
               VALUES (?, ?, ?, ?, ?)""",
            (
                log.get("timestamp", datetime.utcnow().isoformat()),
                log.get("strategy"),
                log.get("symbol"),
                log.get("signal"),
                json.dumps(log.get("details", {})),
            ),
        )
        conn.commit()


# ---------------------------------------------------------------------------
# Cleanup / reset (for testing)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Candle helpers
# ---------------------------------------------------------------------------


def upsert_candle(candle: dict[str, Any]) -> None:
    """Insert or update a candle row (keyed by symbol+timeframe+timestamp).

    On conflict (same symbol+timeframe+timestamp), the OHLC values are merged:
    high takes the max, low takes the min, close is overwritten, volume is accumulated.
    This ensures ticks arriving within the same candle window are merged correctly.
    """
    with _lock:
        conn = _get_conn()
        conn.execute(
            """INSERT INTO candles (symbol, timeframe, timestamp, open, high, low, close, volume)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(symbol, timeframe, timestamp)
               DO UPDATE SET
                   high   = MAX(candles.high, excluded.high),
                   low    = MIN(candles.low,  excluded.low),
                   close  = excluded.close,
                   volume = candles.volume + excluded.volume""",
            (
                candle["symbol"],
                candle.get("timeframe", "1m"),
                int(candle["timestamp"]),
                float(candle.get("open", candle.get("close", 0))),
                float(candle.get("high", candle.get("close", 0))),
                float(candle.get("low", candle.get("close", 0))),
                float(candle.get("close", 0)),
                float(candle.get("volume", 0)),
            ),
        )
        conn.commit()


# Alias for backward compatibility
insert_or_update_candle = upsert_candle


def get_recent_candles(
    symbol: str, timeframe: str = "1m", limit: int = 500
) -> list[dict]:
    """Return the most recent *limit* candles sorted ascending by timestamp.

    This is the primary query used by the /api/candles endpoint and the
    frontend to hydrate the chart on page load.
    """
    conn = _get_conn()
    rows = conn.execute(
        """SELECT symbol, timeframe, timestamp, open, high, low, close, volume
           FROM candles
           WHERE symbol = ? AND timeframe = ?
           ORDER BY timestamp DESC
           LIMIT ?""",
        (symbol, timeframe, limit),
    ).fetchall()
    # Return in chronological (ascending) order
    return [dict(r) for r in reversed(rows)]


# Keep old name working as an alias
get_candles = get_recent_candles


def get_candle_count(symbol: str, timeframe: str = "1m") -> int:
    conn = _get_conn()
    row = conn.execute(
        "SELECT COUNT(*) FROM candles WHERE symbol = ? AND timeframe = ?",
        (symbol, timeframe),
    ).fetchone()
    return row[0] if row else 0


def reset_db() -> None:
    """Drop all rows — useful for tests and fresh demos."""
    with _lock:
        conn = _get_conn()
        for table in ("orders", "trades", "pnl_history", "strategy_logs", "candles"):
            conn.execute(f"DELETE FROM {table}")
        conn.commit()
        logger.warning("Database reset: all rows deleted.")
