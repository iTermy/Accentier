"""Deck listing/items/media for the built-in Kaishi 1.5k deck, plus Anki
progress sync: upload your own Kaishi export (with scheduling) to mark which
words you've already studied, so practice can be filtered to them."""
from __future__ import annotations

import shutil
import uuid

from fastapi import APIRouter, HTTPException, UploadFile
from fastapi.responses import FileResponse

from .. import auth
from ..apkg import ApkgArchive, studied_note_ids
from ..config import MEDIA_DIR, UPLOADS_DIR
from ..db import get_conn, now, row_to_dict, tx
from ..seed import builtin_deck_id

router = APIRouter(prefix="/api", tags=["decks"])


def _accessible_deck(deck_id: int, user_id: int):
    row = get_conn().execute("SELECT * FROM decks WHERE id=?", (deck_id,)).fetchone()
    if not row or not (row["is_builtin"] or row["user_id"] == user_id):
        return None
    return row


@router.get("/decks")
def list_decks(user: dict = auth.CurrentUser):
    rows = get_conn().execute(
        """SELECT d.*,
             (SELECT COUNT(DISTINCT a.item_id) FROM attempts a
                JOIN items i ON i.id = a.item_id
               WHERE i.deck_id = d.id AND a.user_id = :uid) AS practiced_count,
             (SELECT COUNT(*) FROM user_known_items k
                JOIN items i ON i.id = k.item_id
               WHERE i.deck_id = d.id AND k.user_id = :uid) AS known_count
           FROM decks d WHERE d.is_builtin=1 ORDER BY d.id""",
        {"uid": user["id"]},
    ).fetchall()
    return [dict(r) for r in rows]


@router.get("/decks/{deck_id}/items")
def deck_items(deck_id: int, user: dict = auth.CurrentUser):
    deck = _accessible_deck(deck_id, user["id"])
    if not deck:
        raise HTTPException(404, "Deck not found")
    rows = get_conn().execute(
        """SELECT i.id, i.expression, i.reading, i.sentence, i.sentence_audio, i.word_audio,
                  i.word_meaning, i.pitch_notes, i.accent_json,
                  (SELECT 1 FROM user_known_items k
                    WHERE k.item_id = i.id AND k.user_id = ?) AS known,
                  (SELECT MIN(s.due_at) FROM srs_state s
                    WHERE s.item_id = i.id AND s.user_id = ?) AS due_at,
                  (SELECT s.interval_days FROM srs_state s
                    WHERE s.item_id = i.id AND s.user_id = ? ORDER BY s.due_at LIMIT 1) AS interval_days,
                  (SELECT MAX(s.reps) FROM srs_state s
                    WHERE s.item_id = i.id AND s.user_id = ?) AS reps,
                  (SELECT s.last_score FROM srs_state s
                    WHERE s.item_id = i.id AND s.user_id = ? ORDER BY s.due_at LIMIT 1) AS last_score,
                  (SELECT COUNT(*) FROM attempts a WHERE a.item_id = i.id AND a.user_id = ?) AS attempt_count,
                  (SELECT MAX(a.score) FROM attempts a WHERE a.item_id = i.id AND a.user_id = ?) AS best_score
           FROM items i
           WHERE i.deck_id = ?
           ORDER BY i.id""",
        (user["id"], user["id"], user["id"], user["id"], user["id"], user["id"], user["id"], deck_id),
    ).fetchall()
    items = []
    for r in rows:
        d = row_to_dict(r, ("accent_json",))
        # the full sentence word/phrase payloads are heavy × 1500 — the list
        # view only needs the word-level accent summary
        acc = d.get("accent")
        if acc:
            acc.pop("sentence_words", None)
            acc.pop("sentence_phrases", None)
            acc.pop("sentence_hints", None)
        d["known"] = bool(d["known"])
        items.append(d)
    return {"deck": dict(deck), "items": items}


@router.get("/media/{deck_id}/{filename}")
def serve_media(deck_id: int, filename: str, user: dict = auth.CurrentUser):
    if not _accessible_deck(deck_id, user["id"]):
        raise HTTPException(404, "Not found")
    safe = filename.replace("/", "_").replace("\\", "_").replace("..", "_")
    path = MEDIA_DIR / str(deck_id) / safe
    if not path.exists():
        raise HTTPException(404, "Media not found")
    ext = safe.rsplit(".", 1)[-1].lower() if "." in safe else ""
    mt = {
        "mp3": "audio/mpeg", "wav": "audio/wav", "ogg": "audio/ogg",
        "opus": "audio/ogg", "flac": "audio/flac", "m4a": "audio/mp4",
        "aac": "audio/aac", "webm": "audio/webm",
    }.get(ext, "audio/mpeg")
    return FileResponse(path, media_type=mt)


@router.post("/progress/sync")
def sync_progress(file: UploadFile, user: dict = auth.CurrentUser):
    """Mark items as "studied" from the user's own Kaishi export.

    The upload must be an .apkg exported from Anki *with scheduling
    information included* — that's where the studied/new distinction lives.
    Matching is by Anki note id (stable across imports of the shared deck),
    with expression+reading as a fallback for rebuilt decks."""
    if not file.filename or not file.filename.lower().endswith(".apkg"):
        raise HTTPException(400, "Please upload an .apkg file exported from Anki")
    deck_id = builtin_deck_id()
    if deck_id is None:
        raise HTTPException(503, "Built-in deck not seeded yet")

    tmp_path = UPLOADS_DIR / f"{uuid.uuid4().hex}.apkg"
    try:
        with open(tmp_path, "wb") as out:
            shutil.copyfileobj(file.file, out, length=1 << 20)
        try:
            archive = ApkgArchive(tmp_path)
        except Exception as e:
            raise HTTPException(422, f"Could not read .apkg: {e}")
        try:
            conn = archive.open_collection()
            try:
                studied = studied_note_ids(conn)
                total_notes = conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
                note_keys = {}
                if studied:
                    q = f"SELECT id, flds FROM notes WHERE id IN ({','.join('?' * len(studied))})"
                    for r in conn.execute(q, tuple(studied)):
                        fields = r["flds"].split("\x1f")
                        note_keys[r["id"]] = fields[0].strip() if fields else ""
            finally:
                conn.close()
        finally:
            archive.cleanup()
    finally:
        tmp_path.unlink(missing_ok=True)

    if not studied:
        raise HTTPException(
            422,
            "No studied cards found in this file. In Anki, export the deck as .apkg "
            "with “Include scheduling information” checked — otherwise every "
            "card looks new.",
        )

    items = get_conn().execute(
        "SELECT id, note_id, expression FROM items WHERE deck_id=?", (deck_id,)
    ).fetchall()
    by_note = {r["note_id"]: r["id"] for r in items}
    by_expr: dict[str, int] = {}
    for r in items:
        by_expr.setdefault(r["expression"], r["id"])

    matched: set[int] = set()
    for nid in studied:
        item_id = by_note.get(nid) or by_expr.get(note_keys.get(nid, ""))
        if item_id:
            matched.add(item_id)

    with tx() as conn:
        conn.execute("DELETE FROM user_known_items WHERE user_id=?", (user["id"],))
        conn.executemany(
            "INSERT INTO user_known_items (user_id, item_id, synced_at) VALUES (?,?,?)",
            [(user["id"], iid, now()) for iid in matched],
        )

    return {
        "notes_in_file": total_notes,
        "studied_notes": len(studied),
        "known_count": len(matched),
    }


@router.delete("/progress/sync")
def clear_progress(user: dict = auth.CurrentUser):
    with tx() as conn:
        conn.execute("DELETE FROM user_known_items WHERE user_id=?", (user["id"],))
    return {"ok": True, "known_count": 0}
