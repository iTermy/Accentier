"""SQLite persistence layer. Thin, explicit, no ORM."""
import json
import sqlite3
import threading
import time
from contextlib import contextmanager

from .config import DB_PATH

_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS decks (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    name TEXT NOT NULL,
    language TEXT NOT NULL,
    item_count INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY,
    deck_id INTEGER NOT NULL REFERENCES decks(id) ON DELETE CASCADE,
    note_id INTEGER,
    expression TEXT NOT NULL,
    reading TEXT,
    sentence TEXT,
    sentence_audio TEXT,          -- filename under media/<deck_id>/
    word_audio TEXT,
    accent_json TEXT,             -- language-module target data (accent number, moras, source)
    target_json TEXT,             -- cached target F0 analysis (computed lazily)
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_items_deck ON items(deck_id);
CREATE TABLE IF NOT EXISTS attempts (
    id INTEGER PRIMARY KEY,
    item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id),
    mode TEXT NOT NULL DEFAULT 'sentence',   -- 'sentence' | 'word'
    score REAL NOT NULL,
    feedback_json TEXT NOT NULL,
    audio_path TEXT,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_attempts_item ON attempts(item_id, user_id);
CREATE TABLE IF NOT EXISTS srs_state (
    item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id),
    mode TEXT NOT NULL DEFAULT 'sentence',   -- sentence and word shadowing schedule separately
    due_at REAL NOT NULL,
    interval_days REAL NOT NULL DEFAULT 0,
    ease REAL NOT NULL DEFAULT 2.5,
    reps INTEGER NOT NULL DEFAULT 0,
    lapses INTEGER NOT NULL DEFAULT 0,
    last_score REAL,
    PRIMARY KEY (item_id, user_id, mode)
);
"""


def _migrate(conn: sqlite3.Connection) -> None:
    """In-place upgrades for databases created by earlier versions."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(srs_state)")}
    if cols and "mode" not in cols:
        # per-mode scheduling: backfill mode from each item's latest attempt
        conn.executescript("""
            ALTER TABLE srs_state RENAME TO srs_state_v1;
            CREATE TABLE srs_state (
                item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
                user_id INTEGER NOT NULL REFERENCES users(id),
                mode TEXT NOT NULL DEFAULT 'sentence',
                due_at REAL NOT NULL,
                interval_days REAL NOT NULL DEFAULT 0,
                ease REAL NOT NULL DEFAULT 2.5,
                reps INTEGER NOT NULL DEFAULT 0,
                lapses INTEGER NOT NULL DEFAULT 0,
                last_score REAL,
                PRIMARY KEY (item_id, user_id, mode)
            );
            INSERT INTO srs_state (item_id, user_id, mode, due_at, interval_days, ease, reps, lapses, last_score)
                SELECT s.item_id, s.user_id,
                       COALESCE((SELECT a.mode FROM attempts a
                                  WHERE a.item_id = s.item_id AND a.user_id = s.user_id
                                  ORDER BY a.created_at DESC LIMIT 1), 'sentence'),
                       s.due_at, s.interval_days, s.ease, s.reps, s.lapses, s.last_score
                FROM srs_state_v1 s;
            DROP TABLE srs_state_v1;
        """)
        conn.commit()


def get_conn() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(DB_PATH, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        _local.conn = conn
    return conn


def init_db() -> None:
    conn = get_conn()
    _migrate(conn)
    conn.executescript(SCHEMA)
    conn.commit()


@contextmanager
def tx():
    conn = get_conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def now() -> float:
    return time.time()


def row_to_dict(row: sqlite3.Row | None, json_fields: tuple[str, ...] = ()) -> dict | None:
    if row is None:
        return None
    d = dict(row)
    for f in json_fields:
        if f in d and d[f]:
            d[f.removesuffix("_json")] = json.loads(d.pop(f))
        elif f in d:
            d[f.removesuffix("_json")] = None
            d.pop(f, None)
    return d
