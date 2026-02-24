import sqlite3
from contextlib import contextmanager

import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "polyfriends.db") # mount your Railway volume at /data

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    telegram_id INTEGER PRIMARY KEY,
    username    TEXT NOT NULL,
    created_at  TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS groups (
    group_id   INTEGER PRIMARY KEY,
    name       TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
);

-- One membership row per (user, group). This holds the per-group balance.
CREATE TABLE IF NOT EXISTS memberships (
    group_id       INTEGER NOT NULL,
    user_id        INTEGER NOT NULL,
    balance        REAL    DEFAULT 1000.0,
    joined_at      TEXT    DEFAULT (datetime('now')),
    last_refill_at TEXT    DEFAULT (datetime('now')),
    PRIMARY KEY (group_id, user_id),
    FOREIGN KEY (group_id) REFERENCES groups(group_id),
    FOREIGN KEY (user_id)  REFERENCES users(telegram_id)
);

-- Per-group admins
CREATE TABLE IF NOT EXISTS admins (
    group_id  INTEGER NOT NULL,
    user_id   INTEGER NOT NULL,
    PRIMARY KEY (group_id, user_id)
);

CREATE TABLE IF NOT EXISTS markets (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id    INTEGER NOT NULL,
    question    TEXT    NOT NULL,
    created_by  INTEGER NOT NULL,
    approved_by INTEGER,
    status      TEXT    DEFAULT 'pending',
    -- pending | open | closed | resolved | rejected | cancelled
    deadline    TEXT,
    resolution  TEXT,
    -- YES | NO | CANCELLED
    yes_pool    REAL    DEFAULT 10.0,
    no_pool     REAL    DEFAULT 10.0,
    yes_shares  REAL    DEFAULT 0.0,
    no_shares   REAL    DEFAULT 0.0,
    created_at  TEXT    DEFAULT (datetime('now')),
    resolved_at TEXT,
    FOREIGN KEY (group_id)   REFERENCES groups(group_id),
    FOREIGN KEY (created_by) REFERENCES users(telegram_id)
);

-- Each row is one purchase event. Sells create negative-share rows.
CREATE TABLE IF NOT EXISTS positions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL,
    market_id    INTEGER NOT NULL,
    group_id     INTEGER NOT NULL,
    side         TEXT    NOT NULL,
    shares       REAL    NOT NULL,
    points_spent REAL    NOT NULL,
    created_at   TEXT    DEFAULT (datetime('now')),
    FOREIGN KEY (user_id)   REFERENCES users(telegram_id),
    FOREIGN KEY (market_id) REFERENCES markets(id)
);

CREATE INDEX IF NOT EXISTS idx_positions_user_market
    ON positions (user_id, market_id);
CREATE INDEX IF NOT EXISTS idx_markets_group
    ON markets (group_id, status);
"""

@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def init_db():
    with get_db() as conn:
        conn.executescript(SCHEMA)
    print("✅ Database initialized")
