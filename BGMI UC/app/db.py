"""SQLite storage layer.

Uses only the standard library.  One connection per thread keeps the threaded
HTTP server happy without a global lock around reads.
"""

from __future__ import annotations

import secrets
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
    id              TEXT PRIMARY KEY,
    pack_id         TEXT,
    pack_name       TEXT NOT NULL,
    uc_amount       INTEGER NOT NULL,
    bonus_uc        INTEGER NOT NULL DEFAULT 0,
    amount_paise    INTEGER NOT NULL,
    player_id       TEXT NOT NULL,
    player_name     TEXT NOT NULL,
    contact         TEXT,
    buyer_note      TEXT,
    status          TEXT NOT NULL,
    payment_method  TEXT NOT NULL DEFAULT 'upi',
    upi_link        TEXT,
    utr             TEXT,
    paid_at         TEXT,
    admin_note      TEXT,
    delivered_at    TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    expires_at      TEXT
);

CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);
CREATE INDEX IF NOT EXISTS idx_orders_created ON orders(created_at DESC);

CREATE TABLE IF NOT EXISTS order_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id    TEXT NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    event       TEXT NOT NULL,
    detail      TEXT,
    actor       TEXT NOT NULL DEFAULT 'system',
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_order ON order_events(order_id);

CREATE TABLE IF NOT EXISTS feedback (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT,
    rating      INTEGER NOT NULL,
    message     TEXT NOT NULL,
    contact     TEXT,
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_feedback_created ON feedback(created_at DESC);

CREATE TABLE IF NOT EXISTS admin_sessions (
    token       TEXT PRIMARY KEY,
    username    TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    expires_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
"""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None = None) -> str:
    return (dt or utcnow()).isoformat(timespec="seconds")


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def new_order_id() -> str:
    """Human-readable but unguessable order id, e.g. UC-9F3A2C71."""
    return "UC-" + secrets.token_hex(4).upper()


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._local = threading.local()
        # Re-entrant: connect() is called from inside execute(), which already holds it.
        self._write_lock = threading.RLock()
        self._connections: list[sqlite3.Connection] = []

    # -- connection handling ---------------------------------------------------
    def connect(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(self.path), timeout=15, isolation_level=None)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA busy_timeout=5000")
            self._local.conn = conn
            with self._write_lock:
                self._connections.append(conn)
        return conn

    def close_all(self) -> None:
        """Close every connection (used on shutdown and by the tests)."""
        with self._write_lock:
            for conn in self._connections:
                try:
                    conn.close()
                except sqlite3.Error:
                    pass
            self._connections.clear()
        self._local = threading.local()

    def init(self) -> None:
        with self._write_lock:
            conn = self.connect()
            conn.executescript(SCHEMA)

    # -- helpers ---------------------------------------------------------------
    def query(self, sql: str, params: Sequence[Any] | None = None) -> list[sqlite3.Row]:
        return list(self.connect().execute(sql, tuple(params or ())))

    def query_one(self, sql: str, params: Sequence[Any] | None = None) -> sqlite3.Row | None:
        cursor = self.connect().execute(sql, tuple(params or ()))
        return cursor.fetchone()

    def execute(self, sql: str, params: Sequence[Any] | None = None) -> sqlite3.Cursor:
        with self._write_lock:
            return self.connect().execute(sql, tuple(params or ()))

    # -- settings --------------------------------------------------------------
    def get_setting(self, key: str, default: str | None = None) -> str | None:
        row = self.query_one("SELECT value FROM settings WHERE key = ?", [key])
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        self.execute(
            """
            INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value,
                                           updated_at = excluded.updated_at
            """,
            [key, value, iso()],
        )

    # -- order events ----------------------------------------------------------
    def add_event(self, order_id: str, event: str, detail: str | None = None,
                  actor: str = "system") -> None:
        self.execute(
            """
            INSERT INTO order_events (order_id, event, detail, actor, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            [order_id, event, detail, actor, iso()],
        )

    def order_events(self, order_id: str) -> list[sqlite3.Row]:
        return self.query(
            "SELECT event, detail, actor, created_at FROM order_events "
            "WHERE order_id = ? ORDER BY id ASC",
            [order_id],
        )

    def orders_needing_expiry(self) -> Iterable[sqlite3.Row]:
        return self.query(
            "SELECT id, expires_at FROM orders WHERE status = 'pending_payment' "
            "AND expires_at IS NOT NULL AND expires_at < ?",
            [iso()],
        )


def expires_at(minutes: int) -> str:
    return iso(utcnow() + timedelta(minutes=minutes))
