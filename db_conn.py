"""
Single connection manager for all modules.
Every module should use get_conn() from here instead of creating its own.
"""
import sqlite3
import logging
import threading
from pathlib import Path

logger = logging.getLogger("db")

DB_PATH = Path(__file__).parent / "leads.db"

_conn = None
_lock = threading.Lock()


def get_conn() -> sqlite3.Connection:
    """Get a single shared connection (thread-safe via lock)."""
    global _conn
    with _lock:
        if _conn is None:
            _conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
            _conn.row_factory = sqlite3.Row
            _conn.execute("PRAGMA foreign_keys=ON")
            _conn.execute("PRAGMA journal_mode=WAL")
            logger.info("DB connection created")
        return _conn


def close_conn():
    """Close the shared connection (for graceful shutdown)."""
    global _conn
    with _lock:
        if _conn:
            _conn.close()
            _conn = None
            logger.info("DB connection closed")


def execute(sql: str, params: list | tuple = ()) -> list[dict]:
    """Execute a query and return list of dicts."""
    conn = get_conn()
    rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def execute_one(sql: str, params: list | tuple = ()) -> dict | None:
    """Execute a query and return first row as dict."""
    conn = get_conn()
    row = conn.execute(sql, params).fetchone()
    return dict(row) if row else None


def execute_write(sql: str, params: list | tuple = ()) -> int:
    """Execute a write query and return lastrowid."""
    conn = get_conn()
    cur = conn.execute(sql, params)
    conn.commit()
    return cur.lastrowid or 0
