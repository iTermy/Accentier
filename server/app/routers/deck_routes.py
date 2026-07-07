"""Deck upload / listing. Upload streams the .apkg to disk, parses notes,
extracts referenced audio, builds per-item accent data via the language module."""
from __future__ import annotations

import json
import shutil
import uuid

from fastapi import APIRouter, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from .. import auth
from ..apkg import parse_apkg
from ..config import MEDIA_DIR, UPLOADS_DIR
from ..db import get_conn, now, row_to_dict, tx
from ..languages.base import detect_language, get_module

router = APIRouter(prefix="/api", tags=["decks"])


@router.post("/decks/upload")
def upload_deck(file: UploadFile, language: str = Form("auto"), user: dict = auth.CurrentUser):
    if not file.filename or not file.filename.lower().endswith(".apkg"):
        raise HTTPException(400, "Please upload an .apkg file exported from Anki")

    tmp_path = UPLOADS_DIR / f"{uuid.uuid4().hex}.apkg"
    try:
        with open(tmp_path, "wb") as out:
            shutil.copyfileobj(file.file, out, length=1 << 20)

        deck_name = file.filename.rsplit(".", 1)[0]
        try:
            parsed, archive = parse_apkg(tmp_path, deck_name)
        except Exception as e:
            raise HTTPException(422, f"Could not parse .apkg: {e}")

        try:
            if not parsed.notes:
                raise HTTPException(422, "No usable notes found in this deck")

            if language == "auto":
                sample = [n.expression + " " + n.sentence for n in parsed.notes[:50]]
                language = detect_language(sample)
            module = get_module(language)

            with tx() as conn:
                cur = conn.execute(
                    "INSERT INTO decks (user_id, name, language, created_at) VALUES (?,?,?,?)",
                    (user["id"], parsed.name, language, now()),
                )
                deck_id = cur.lastrowid

            deck_media = MEDIA_DIR / str(deck_id)
            deck_media.mkdir(parents=True, exist_ok=True)
            extracted = set()
            skipped_no_audio = 0

            with tx() as conn:
                for note in parsed.notes:
                    if not note.sentence_audio and not note.word_audio:
                        skipped_no_audio += 1
                        continue
                    for fname in (note.sentence_audio, note.word_audio):
                        if fname and fname not in extracted:
                            data = archive.read_media(fname)
                            if data:
                                # sanitize: media names come from the deck, keep basename only
                                safe = fname.replace("/", "_").replace("\\", "_")
                                (deck_media / safe).write_bytes(data)
                                extracted.add(fname)
                    accent = module.build_accent_data(note)
                    conn.execute(
                        """INSERT INTO items (deck_id, note_id, expression, reading, sentence,
                             sentence_audio, word_audio, accent_json, created_at)
                           VALUES (?,?,?,?,?,?,?,?,?)""",
                        (deck_id, note.note_id, note.expression, note.reading, note.sentence,
                         note.sentence_audio.replace("/", "_").replace("\\", "_") if note.sentence_audio else "",
                         note.word_audio.replace("/", "_").replace("\\", "_") if note.word_audio else "",
                         json.dumps(accent, ensure_ascii=False), now()),
                    )
                count = conn.execute("SELECT COUNT(*) FROM items WHERE deck_id=?", (deck_id,)).fetchone()[0]
                conn.execute("UPDATE decks SET item_count=? WHERE id=?", (count, deck_id))
        finally:
            archive.cleanup()

        return {
            "deck_id": deck_id,
            "name": parsed.name,
            "language": language,
            "items_imported": count,
            "skipped_no_audio": skipped_no_audio,
            "media_extracted": len(extracted),
        }
    finally:
        tmp_path.unlink(missing_ok=True)


@router.get("/decks")
def list_decks(user: dict = auth.CurrentUser):
    rows = get_conn().execute(
        """SELECT d.*,
             (SELECT COUNT(DISTINCT a.item_id) FROM attempts a
                JOIN items i ON i.id = a.item_id
               WHERE i.deck_id = d.id AND a.user_id = ?) AS practiced_count
           FROM decks d WHERE d.user_id=? ORDER BY d.created_at DESC""",
        (user["id"], user["id"]),
    ).fetchall()
    return [dict(r) for r in rows]


@router.delete("/decks/{deck_id}")
def delete_deck(deck_id: int, user: dict = auth.CurrentUser):
    row = get_conn().execute("SELECT * FROM decks WHERE id=? AND user_id=?", (deck_id, user["id"])).fetchone()
    if not row:
        raise HTTPException(404, "Deck not found")
    with tx() as conn:
        conn.execute("DELETE FROM decks WHERE id=?", (deck_id,))
    shutil.rmtree(MEDIA_DIR / str(deck_id), ignore_errors=True)
    return {"ok": True}


@router.get("/decks/{deck_id}/items")
def deck_items(deck_id: int, user: dict = auth.CurrentUser):
    deck = get_conn().execute("SELECT * FROM decks WHERE id=? AND user_id=?", (deck_id, user["id"])).fetchone()
    if not deck:
        raise HTTPException(404, "Deck not found")
    rows = get_conn().execute(
        """SELECT i.id, i.expression, i.reading, i.sentence, i.sentence_audio, i.word_audio,
                  i.accent_json,
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
        (user["id"], user["id"], user["id"], user["id"], user["id"], user["id"], deck_id),
    ).fetchall()
    return {"deck": dict(deck), "items": [row_to_dict(r, ("accent_json",)) for r in rows]}


@router.get("/media/{deck_id}/{filename}")
def serve_media(deck_id: int, filename: str, user: dict = auth.CurrentUser):
    deck = get_conn().execute("SELECT id FROM decks WHERE id=? AND user_id=?", (deck_id, user["id"])).fetchone()
    if not deck:
        raise HTTPException(404, "Not found")
    safe = filename.replace("/", "_").replace("\\", "_").replace("..", "_")
    path = MEDIA_DIR / str(deck_id) / safe
    if not path.exists():
        raise HTTPException(404, "Media not found")
    mt = "audio/mpeg" if not safe.lower().endswith((".wav", ".ogg", ".opus", ".flac")) else None
    return FileResponse(path, media_type=mt)
