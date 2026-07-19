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
    user_id INTEGER REFERENCES users(id),   -- NULL for the built-in deck
    name TEXT NOT NULL,
    language TEXT NOT NULL,
    item_count INTEGER NOT NULL DEFAULT 0,
    is_builtin INTEGER NOT NULL DEFAULT 0,
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
    word_meaning TEXT,
    sentence_meaning TEXT,
    pitch_notes TEXT,             -- curator notes from the deck's Pitch Accent Notes field
    accent_json TEXT,             -- language-module target data (accent number, moras, source)
    target_json TEXT,             -- cached target F0 analysis (computed lazily)
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_items_deck ON items(deck_id);
CREATE TABLE IF NOT EXISTS user_known_items (
    user_id INTEGER NOT NULL REFERENCES users(id),
    item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    synced_at REAL NOT NULL,
    PRIMARY KEY (user_id, item_id)
);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
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
    deck_info = list(conn.execute("PRAGMA table_info(decks)"))
    deck_cols = {r[1] for r in deck_info}
    if deck_cols and "is_builtin" not in deck_cols:
        conn.execute("ALTER TABLE decks ADD COLUMN is_builtin INTEGER NOT NULL DEFAULT 0")
        conn.commit()
    # user_id must be nullable now (the built-in deck has no owner)
    if any(r[1] == "user_id" and r[3] for r in deck_info):
        conn.executescript("""
            CREATE TABLE decks_v2 (
                id INTEGER PRIMARY KEY,
                user_id INTEGER REFERENCES users(id),
                name TEXT NOT NULL,
                language TEXT NOT NULL,
                item_count INTEGER NOT NULL DEFAULT 0,
                is_builtin INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL
            );
            INSERT INTO decks_v2 (id, user_id, name, language, item_count, is_builtin, created_at)
                SELECT id, user_id, name, language, item_count,
                       COALESCE(is_builtin, 0), created_at FROM decks;
            DROP TABLE decks;
            ALTER TABLE decks_v2 RENAME TO decks;
        """)
        conn.commit()

    item_cols = {r[1] for r in conn.execute("PRAGMA table_info(items)")}
    for col in ("word_meaning", "sentence_meaning", "pitch_notes"):
        if item_cols and col not in item_cols:
            conn.execute(f"ALTER TABLE items ADD COLUMN {col} TEXT")
    conn.commit()

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
