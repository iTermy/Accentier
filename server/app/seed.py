"""Built-in Kaishi 1.5k deck: imported from resources/ at startup.

The deck row has user_id NULL + is_builtin=1 and is shared by every account
(attempts/SRS state are per-user already). Seeding is idempotent and
versioned: bump SEED_VERSION to force a re-import (items are upserted by
Anki note id so item ids — and with them user attempts and SRS state —
survive re-seeds; cached target analyses are invalidated).
"""
from __future__ import annotations

import json
import time

from .apkg import parse_apkg
from .config import KAISHI_APKG, MEDIA_DIR
from .db import get_conn, now, tx
from .languages.base import get_module

# bump when the importer/accent pipeline changes and items must be rebuilt
SEED_VERSION = 1

DECK_NAME = "Kaishi 1.5k"
LANGUAGE = "ja"


def _meta_get(key: str) -> str | None:
    row = get_conn().execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else None


def _meta_set(key: str, value: str) -> None:
    with tx() as conn:
        conn.execute("INSERT INTO meta (key, value) VALUES (?,?) "
                     "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))


def builtin_deck_id() -> int | None:
    row = get_conn().execute("SELECT id FROM decks WHERE is_builtin=1").fetchone()
    return row["id"] if row else None


def ensure_kaishi_deck() -> None:
    deck_id = builtin_deck_id()
    if deck_id is not None and _meta_get("kaishi_seed_version") == str(SEED_VERSION):
        return
    if not KAISHI_APKG.exists():
        print(f"[seed] {KAISHI_APKG} not found — built-in deck unavailable")
        return

    t0 = time.time()
    print(f"[seed] importing {DECK_NAME} from {KAISHI_APKG.name} …")
    parsed, archive = parse_apkg(KAISHI_APKG, DECK_NAME)
    try:
        module = get_module(LANGUAGE)

        if deck_id is None:
            with tx() as conn:
                cur = conn.execute(
                    "INSERT INTO decks (user_id, name, language, is_builtin, created_at) "
                    "VALUES (NULL,?,?,1,?)",
                    (DECK_NAME, LANGUAGE, now()),
                )
                deck_id = cur.lastrowid

        deck_media = MEDIA_DIR / str(deck_id)
        deck_media.mkdir(parents=True, exist_ok=True)

        def safe_name(fname: str) -> str:
            return fname.replace("/", "_").replace("\\", "_").replace("..", "_")

        existing = {
            r["note_id"]: r["id"]
            for r in get_conn().execute("SELECT id, note_id FROM items WHERE deck_id=?", (deck_id,))
        }
        seen_note_ids: set[int] = set()
        n_media = n_new = n_updated = 0

        with tx() as conn:
            for note in parsed.notes:
                if not note.sentence_audio and not note.word_audio:
                    continue
                for fname in (note.sentence_audio, note.word_audio):
                    if not fname:
                        continue
                    dest = deck_media / safe_name(fname)
                    if not dest.exists():
                        data = archive.read_media(fname)
                        if data:
                            dest.write_bytes(data)
                            n_media += 1
                accent = module.build_accent_data(note)
                fields = (
                    note.expression, note.reading, note.sentence,
                    safe_name(note.sentence_audio) if note.sentence_audio else "",
                    safe_name(note.word_audio) if note.word_audio else "",
                    note.word_meaning, note.sentence_meaning, note.pitch_notes,
                    json.dumps(accent, ensure_ascii=False),
                )
                seen_note_ids.add(note.note_id)
                item_id = existing.get(note.note_id)
                if item_id is None:
                    conn.execute(
                        """INSERT INTO items (deck_id, note_id, expression, reading, sentence,
                             sentence_audio, word_audio, word_meaning, sentence_meaning,
                             pitch_notes, accent_json, created_at)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (deck_id, note.note_id, *fields, now()),
                    )
                    n_new += 1
                else:
                    conn.execute(
                        """UPDATE items SET expression=?, reading=?, sentence=?,
                             sentence_audio=?, word_audio=?, word_meaning=?,
                             sentence_meaning=?, pitch_notes=?, accent_json=?,
                             target_json=NULL
                           WHERE id=?""",
                        (*fields, item_id),
                    )
                    n_updated += 1
            removed = set(existing) - seen_note_ids
            if removed:
                conn.execute(
                    f"DELETE FROM items WHERE deck_id=? AND note_id IN "
                    f"({','.join('?' * len(removed))})",
                    (deck_id, *removed),
                )
            count = conn.execute("SELECT COUNT(*) FROM items WHERE deck_id=?", (deck_id,)).fetchone()[0]
            conn.execute("UPDATE decks SET item_count=?, name=? WHERE id=?", (count, DECK_NAME, deck_id))
    finally:
        archive.cleanup()

    _meta_set("kaishi_seed_version", str(SEED_VERSION))
    print(f"[seed] done in {time.time() - t0:.1f}s — {n_new} new, {n_updated} updated, "
          f"{n_media} media files extracted")
