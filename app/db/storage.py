    """
app/db/storage.py
=================
SQLite-backed persistence â€” **single source of truth** for:

    - accounts  (capital, realised PnL)
    - positions (per-symbol qty, avg_price, side)
    - orders, trades, PnL history, strategy logs, candles

Thread-safe: uses ``check_same_thread=False`` and a module-level lock
for all writes.  All timestamps use ``EngineClock.now_iso()`` (UTC ISO 8601).
"""

import sqlite3
import threading
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


def _utc_now() -> str:
    """Return current UTC time as ISO 8601 string.

    Single chokepoint for all DB timestamps.  Uses EngineClock when
    available (runtime), falls back to datetime.now(timezone.utc)
    during early init before the clock is instantiated.
    """
    try:
        from app.utils.clock import EngineClock

        return EngineClock(mode="demo").now_iso()
    except Exception:
        return datetime.now(timezone.utc).isoformat()


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
        -- â”€â”€ Account table (DB-backed capital) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        CREATE TABLE IF NOT EXISTS accounts (
            account_id        TEXT PRIMARY KEY DEFAULT 'default',
            initial_capital   REAL NOT NULL,
            available_capital REAL NOT NULL,
            realised_pnl      REAL NOT NULL DEFAULT 0,
            created_at        TEXT NOT NULL,
            updated_at        TEXT NOT NULL
        );

        -- â”€â”€ Positions table (DB-backed position book) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        CREATE TABLE IF NOT EXISTS positions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id  TEXT NOT NULL DEFAULT 'default',
            symbol      TEXT NOT NULL,
            side        TEXT NOT NULL DEFAULT 'FLAT',
            qty         INTEGER NOT NULL DEFAULT 0,
            avg_price   REAL NOT NULL DEFAULT 0,
            updated_at  TEXT NOT NULL,
            UNIQUE(account_id, symbol),
            FOREIGN KEY (account_id) REFERENCES accounts(account_id)
        );

        -- â”€â”€ Orders â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        CREATE TABLE IF NOT EXISTS orders (
            order_id   TEXT PRIMARY KEY,
            account_id TEXT DEFAULT 'default',
            symbol     TEXT NOT NULL,
            side       TEXT NOT NULL,
            qty        INTEGER NOT NULL,
            price      REAL,
            order_type TEXT DEFAULT 'MARKET',
            status     TEXT DEFAULT 'NEW',
            filled_qty INTEGER DEFAULT 0,
            avg_price  REAL DEFAULT 0,
            strategy   TEXT,
            created_at TEXT,
            updated_at TEXT
        );

        -- â”€â”€ Trades â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        CREATE TABLE IF NOT EXISTS trades (
            trade_id   INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id   TEXT,
            account_id TEXT DEFAULT 'default',
            symbol     TEXT,
            side       TEXT,
            qty        INTEGER,
            price      REAL,
            pnl        REAL DEFAULT 0,
            timestamp  TEXT,
            FOREIGN KEY (order_id) REFERENCES orders(order_id)
        );

        -- â”€â”€ PnL history â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        CREATE TABLE IF NOT EXISTS pnl_history (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id     TEXT DEFAULT 'default',
            timestamp      TEXT,
            realised_pnl   REAL,
            unrealised_pnl REAL,
            total_pnl      REAL,
            capital        REAL
        );

        -- â”€â”€ Strategy logs â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        CREATE TABLE IF NOT EXISTS strategy_logs (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp  TEXT,
            strategy   TEXT,
            symbol     TEXT,
            signal     TEXT,
            details    TEXT
        );

        -- â”€â”€ Candles â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
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
        CREATE INDEX IF NOT EXISTS idx_positions_account
            ON positions(account_id);
        """
    )
    conn.commit()

    # â”€â”€ Safe migrations for existing databases â”€â”€
    _migrate_add_columns(conn)

    # â”€â”€ Create indexes that depend on migrated columns â”€â”€
    # These must run AFTER migrations add account_id to older tables.
    try:
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_orders_account "
            "ON orders(account_id, created_at DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_trades_account "
            "ON trades(account_id, timestamp DESC)"
        )
        conn.commit()
    except sqlite3.OperationalError:
        pass  # column may still not exist in edge cases

    logger.info("Database tables initialised at %s", DB_PATH)


def _migrate_add_columns(conn: sqlite3.Connection) -> None:
    """Add new columns to existing tables if they don't exist yet.

    SQLite lacks ``ALTER TABLE â€¦ ADD COLUMN IF NOT EXISTS`` so we use
    a try/except for each migration.
    """
    migrations = [
        ("orders", "account_id", "TEXT DEFAULT 'default'"),
        ("trades", "account_id", "TEXT DEFAULT 'default'"),
        ("trades", "pnl", "REAL DEFAULT 0"),
        ("pnl_history", "account_id", "TEXT DEFAULT 'default'"),
        ("accounts", "daily_loss_halted", "INTEGER DEFAULT 0"),
        ("accounts", "engine_state", "TEXT DEFAULT 'IDLE'"),
    ]
    for table, col, col_type in migrations:
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}")
            conn.commit()
            logger.info("Migration: added %s.%s", table, col)
        except sqlite3.OperationalError:
            pass  # column already exists

    # -- Create users table for multi-user support --
    try:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS users (
                user_id    TEXT PRIMARY KEY,
                username   TEXT UNIQUE NOT NULL,
                password   TEXT NOT NULL,
                created_at TEXT NOT NULL
            )"""
        )
        conn.commit()
    except sqlite3.OperationalError:
        pass

    # -- Add user_id to accounts if missing --
    try:
        conn.execute("ALTER TABLE accounts ADD COLUMN user_id TEXT DEFAULT 'default'")
        conn.commit()
        logger.info("Migration: added accounts.user_id")
    except sqlite3.OperationalError:
        pass


# ===========================================================================
#  USER helpers (dummy multi-user)
# ===========================================================================


def create_user(user_id: str, username: str, password_hash: str) -> dict:
    """Create a new user. Returns user dict."""
    with _lock:
        conn = _get_conn()
        now = _utc_now()
        conn.execute(
            "INSERT INTO users (user_id, username, password, created_at) VALUES (?, ?, ?, ?)",
            (user_id, username, password_hash, now),
        )
        conn.commit()
    return {"user_id": user_id, "username": username, "created_at": now}


def get_user_by_username(username: str) -> Optional[dict]:
    """Look up a user by username."""
    conn = _get_conn()
    row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    return dict(row) if row else None


def get_user_by_id(user_id: str) -> Optional[dict]:
    """Look up a user by ID."""
    conn = _get_conn()
    row = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
    return dict(row) if row else None


# ===========================================================================
#  ACCOUNT helpers  (DB-backed capital management)
# ===========================================================================


def ensure_default_account(initial_capital: float, account_id: str = "default") -> dict:
    """Create the default account row if it doesn't exist.

    Returns the account dict.  If the account already exists it is
    returned as-is (capital is NOT reset â€” that's the whole point).
    """
    with _lock:
        conn = _get_conn()
        row = conn.execute(
            "SELECT * FROM accounts WHERE account_id = ?", (account_id,)
        ).fetchone()
        if row:
            return dict(row)

        now = _utc_now()
        conn.execute(
            """INSERT INTO accounts
               (account_id, initial_capital, available_capital, realised_pnl,
                created_at, updated_at)
               VALUES (?, ?, ?, 0, ?, ?)""",
            (account_id, initial_capital, initial_capital, now, now),
        )
        conn.commit()
        logger.info(
            "Created account %s with initial_capital=%.2f",
            account_id,
            initial_capital,
        )
        row = conn.execute(
            "SELECT * FROM accounts WHERE account_id = ?", (account_id,)
        ).fetchone()
        return dict(row)


def get_account(account_id: str = "default") -> Optional[dict]:
    """Return account dict or None."""
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM accounts WHERE account_id = ?", (account_id,)
    ).fetchone()
    return dict(row) if row else None


def update_account(
    account_id: str,
    available_capital: float,
    realised_pnl: float,
) -> None:
    """Persist capital and realised PnL to the accounts table."""
    with _lock:
        conn = _get_conn()
        conn.execute(
            """UPDATE accounts
               SET available_capital = ?, realised_pnl = ?, updated_at = ?
               WHERE account_id = ?""",
            (
                available_capital,
                realised_pnl,
                _utc_now(),
                account_id,
            ),
        )
        conn.commit()


def update_daily_loss_halted(account_id: str, halted: bool) -> None:
    """Persist daily_loss_halted flag to DB."""
    with _lock:
        conn = _get_conn()
        conn.execute(
            "UPDATE accounts SET daily_loss_halted = ? WHERE account_id = ?",
            (1 if halted else 0, account_id),
        )
        conn.commit()


def update_engine_state(state: str, account_id: str = "default") -> None:
    """Persist engine state to DB for auto-resume on restart."""
    with _lock:
        conn = _get_conn()
        conn.execute(
            "UPDATE accounts SET engine_state = ?, updated_at = ? WHERE account_id = ?",
            (state, _utc_now(), account_id),
        )
        conn.commit()


def get_engine_state(account_id: str = "default") -> str:
    """Get persisted engine state (for auto-resume)."""
    conn = _get_conn()
    row = conn.execute(
        "SELECT engine_state FROM accounts WHERE account_id = ?", (account_id,)
    ).fetchone()
    if row:
        try:
            return row["engine_state"] or "IDLE"
        except (IndexError, KeyError):
            return "IDLE"
    return "IDLE"


def reset_account(account_id: str = "default", initial_capital: float = 0) -> None:
    """Reset an account to its initial state (for fresh demos)."""
    with _lock:
        conn = _get_conn()
        acct = conn.execute(
            "SELECT initial_capital FROM accounts WHERE account_id = ?",
            (account_id,),
        ).fetchone()
        cap = initial_capital or (acct["initial_capital"] if acct else 1_000_000)
        now = _utc_now()
        conn.execute(
            """UPDATE accounts
               SET available_capital = ?, realised_pnl = 0, updated_at = ?
               WHERE account_id = ?""",
            (cap, now, account_id),
        )
        conn.execute("DELETE FROM positions WHERE account_id = ?", (account_id,))
        conn.commit()
        logger.info("Account %s reset to %.2f", account_id, cap)


# ===========================================================================
#  POSITION helpers  (DB-backed position book)
# ===========================================================================


def upsert_position(
    symbol: str,
    side: str,
    qty: int,
    avg_price: float,
    account_id: str = "default",
) -> None:
    """Insert or update a position row.

    If qty == 0, the position is deleted (FLAT).
    """
    now = _utc_now()
    with _lock:
        conn = _get_conn()
        if qty <= 0:
            conn.execute(
                "DELETE FROM positions WHERE account_id = ? AND symbol = ?",
                (account_id, symbol),
            )
        else:
            conn.execute(
                """INSERT INTO positions (account_id, symbol, side, qty, avg_price, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(account_id, symbol)
                   DO UPDATE SET side = ?, qty = ?, avg_price = ?, updated_at = ?""",
                (
                    account_id,
                    symbol,
                    side,
                    qty,
                    avg_price,
                    now,
                    side,
                    qty,
                    avg_price,
                    now,
                ),
            )
        conn.commit()


def get_positions(account_id: str = "default") -> list[dict]:
    """Return all open positions for an account."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM positions WHERE account_id = ? AND qty > 0",
        (account_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_position(symbol: str, account_id: str = "default") -> Optional[dict]:
    """Return a single position dict or None."""
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM positions WHERE account_id = ? AND symbol = ?",
        (account_id, symbol),
    ).fetchone()
    return dict(row) if row else None


def delete_all_positions(account_id: str = "default") -> None:
    """Remove all positions for an account."""
    with _lock:
        conn = _get_conn()
        conn.execute("DELETE FROM positions WHERE account_id = ?", (account_id,))
        conn.commit()


# ===========================================================================
#  ORDER helpers
# ===========================================================================


def insert_order(order: dict[str, Any]) -> None:
    """Insert a new order row."""
    with _lock:
        conn = _get_conn()
        conn.execute(
            """INSERT INTO orders
               (order_id, account_id, symbol, side, qty, price, order_type,
                status, filled_qty, avg_price, strategy, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                order["order_id"],
                order.get("account_id", "default"),
                order["symbol"],
                order["side"],
                order["qty"],
                order.get("price"),
                order.get("order_type", "MARKET"),
                order.get("status", "NEW"),
                order.get("filled_qty", 0),
                order.get("avg_price", 0),
                order.get("strategy"),
                order.get("created_at", _utc_now()),
                order.get("updated_at", _utc_now()),
            ),
        )
        conn.commit()


def update_order(order_id: str, updates: dict[str, Any]) -> None:
    """Update order fields by order_id."""
    with _lock:
        conn = _get_conn()
        updates["updated_at"] = _utc_now()
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
        "SELECT * FROM orders WHERE status IN ('NEW','ACK','PARTIAL') "
        "ORDER BY created_at DESC"
    ).fetchall()
    return [dict(r) for r in rows]


# ===========================================================================
#  TRADE helpers
# ===========================================================================


def insert_trade(trade: dict[str, Any]) -> None:
    with _lock:
        conn = _get_conn()
        conn.execute(
            """INSERT INTO trades
               (order_id, account_id, symbol, side, qty, price, pnl, timestamp)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                trade["order_id"],
                trade.get("account_id", "default"),
                trade["symbol"],
                trade["side"],
                trade["qty"],
                trade["price"],
                trade.get("pnl", 0),
                trade.get("timestamp", _utc_now()),
            ),
        )
        conn.commit()


def insert_order_and_trade(order: dict[str, Any], trade: dict[str, Any]) -> None:
    """Insert/update an order and its trade fill in a single transaction."""
    with _lock:
        conn = _get_conn()
        try:
            conn.execute("BEGIN")
            now = _utc_now()
            updates = {
                "status": order.get("status", "FILLED"),
                "filled_qty": order.get("filled_qty", 0),
                "avg_price": order.get("avg_price", 0),
                "updated_at": now,
            }
            set_clause = ", ".join(f"{k} = ?" for k in updates)
            values = list(updates.values()) + [order["order_id"]]
            conn.execute(f"UPDATE orders SET {set_clause} WHERE order_id = ?", values)
            conn.execute(
                """INSERT INTO trades
                   (order_id, account_id, symbol, side, qty, price, pnl, timestamp)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    trade["order_id"],
                    trade.get("account_id", "default"),
                    trade["symbol"],
                    trade["side"],
                    trade["qty"],
                    trade["price"],
                    trade.get("pnl", 0),
                    trade.get("timestamp", now),
                ),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def get_trades(limit: int = 200, account_id: str = "default") -> list[dict]:
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM trades WHERE account_id = ? ORDER BY timestamp DESC LIMIT ?",
        (account_id, limit),
    ).fetchall()
    return [dict(r) for r in rows]


# ===========================================================================
#  PNL HISTORY
# ===========================================================================


def insert_pnl_snapshot(snapshot: dict[str, Any]) -> None:
    with _lock:
        conn = _get_conn()
        conn.execute(
            """INSERT INTO pnl_history
               (account_id, timestamp, realised_pnl, unrealised_pnl, total_pnl, capital)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                snapshot.get("account_id", "default"),
                snapshot.get("timestamp", _utc_now()),
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


# ===========================================================================
#  STRATEGY LOGS
# ===========================================================================


def insert_strategy_log(log: dict[str, Any]) -> None:
    with _lock:
        conn = _get_conn()
        conn.execute(
            """INSERT INTO strategy_logs (timestamp, strategy, symbol, signal, details)
               VALUES (?, ?, ?, ?, ?)""",
            (
                log.get("timestamp", _utc_now()),
                log.get("strategy"),
                log.get("symbol"),
                log.get("signal"),
                json.dumps(log.get("details", {})),
            ),
        )
        conn.commit()


# ===========================================================================
#  CANDLE helpers
# ===========================================================================


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
    """Return the most recent *limit* candles sorted ascending by timestamp."""
    conn = _get_conn()
    rows = conn.execute(
        """SELECT symbol, timeframe, timestamp, open, high, low, close, volume
           FROM candles
           WHERE symbol = ? AND timeframe = ?
           ORDER BY timestamp DESC
           LIMIT ?""",
        (symbol, timeframe, limit),
    ).fetchall()
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


# ===========================================================================
#  RESET / CLEANUP
# ===========================================================================


def reset_db() -> None:
    """Drop all rows â€” useful for tests and fresh demos."""
    with _lock:
        conn = _get_conn()
        for table in (
            "positions",
            "orders",
            "trades",
            "pnl_history",
            "strategy_logs",
            "candles",
            "accounts",
        ):
            conn.execute(f"DELETE FROM {table}")
        conn.commit()
        logger.warning("Database reset: all rows deleted.")
