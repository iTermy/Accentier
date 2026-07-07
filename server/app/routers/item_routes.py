"""Item detail, attempt submission (the record→analyze→score step), history."""
from __future__ import annotations

import json

from fastapi import APIRouter, Form, HTTPException, UploadFile

from .. import auth, srs
from ..analysis import analyze_attempt, ensure_accent_estimate, get_target_analysis
from ..config import ATTEMPTS_DIR
from ..db import get_conn, now, row_to_dict, tx

router = APIRouter(prefix="/api", tags=["items"])

MAX_ATTEMPT_BYTES = 30 * 1024 * 1024


def _load_item(item_id: int, user_id: int) -> dict:
    row = get_conn().execute(
        """SELECT i.*, d.language, d.user_id AS owner, d.name AS deck_name
           FROM items i JOIN decks d ON d.id = i.deck_id WHERE i.id=?""",
        (item_id,),
    ).fetchone()
    if not row or row["owner"] != user_id:
        raise HTTPException(404, "Item not found")
    return row_to_dict(row, ("accent_json", "target_json"))


@router.get("/items/{item_id}")
def item_detail(item_id: int, user: dict = auth.CurrentUser):
    item = _load_item(item_id, user["id"])
    item["accent"] = ensure_accent_estimate(item, item["language"])
    targets = {}
    for mode in ("sentence", "word"):
        t = get_target_analysis(item, mode)
        if t:
            targets[mode] = t
    srs_rows = get_conn().execute(
        "SELECT * FROM srs_state WHERE item_id=? AND user_id=?", (item_id, user["id"])
    ).fetchall()
    return {
        "id": item["id"],
        "deck_id": item["deck_id"],
        "deck_name": item["deck_name"],
        "language": item["language"],
        "expression": item["expression"],
        "reading": item["reading"],
        "sentence": item["sentence"],
        "sentence_audio": item["sentence_audio"],
        "word_audio": item["word_audio"],
        "accent": item["accent"],
        "targets": targets,
        "srs": {r["mode"]: dict(r) for r in srs_rows},
    }


@router.post("/items/{item_id}/attempts")
def submit_attempt(item_id: int, audio: UploadFile, mode: str = Form("sentence"),
                   user: dict = auth.CurrentUser):
    if mode not in ("sentence", "word"):
        raise HTTPException(400, "mode must be 'sentence' or 'word'")
    item = _load_item(item_id, user["id"])
    blob = audio.file.read(MAX_ATTEMPT_BYTES + 1)
    if len(blob) > MAX_ATTEMPT_BYTES:
        raise HTTPException(413, "Recording too large")
    if len(blob) < 100:
        raise HTTPException(400, "Empty recording")

    try:
        result = analyze_attempt(item, item["language"], blob, mode)
    except ValueError as e:
        raise HTTPException(422, str(e))

    with tx() as conn:
        cur = conn.execute(
            """INSERT INTO attempts (item_id, user_id, mode, score, feedback_json, created_at)
               VALUES (?,?,?,?,?,?)""",
            (item_id, user["id"], mode, result["score"],
             json.dumps(result, ensure_ascii=False), now()),
        )
        attempt_id = cur.lastrowid
    # keep the recording for playback in history
    audio_path = ATTEMPTS_DIR / f"{attempt_id}.wav"
    audio_path.write_bytes(blob)
    with tx() as conn:
        conn.execute("UPDATE attempts SET audio_path=? WHERE id=?", (str(audio_path.name), attempt_id))

    schedule = srs.record_result(user["id"], item_id, mode, result["score"])
    return {"attempt_id": attempt_id, "result": result, "srs": schedule}


@router.get("/items/{item_id}/attempts")
def attempt_history(item_id: int, user: dict = auth.CurrentUser):
    _load_item(item_id, user["id"])
    rows = get_conn().execute(
        """SELECT id, mode, score, created_at FROM attempts
           WHERE item_id=? AND user_id=? ORDER BY created_at DESC LIMIT 50""",
        (item_id, user["id"]),
    ).fetchall()
    return [dict(r) for r in rows]


@router.get("/review/queue")
def review_queue(user: dict = auth.CurrentUser):
    rows = get_conn().execute(
        """SELECT i.id, i.expression, i.reading, i.sentence, i.accent_json, d.name AS deck_name,
                  d.language, s.mode, s.due_at, s.interval_days, s.reps, s.last_score
           FROM srs_state s
           JOIN items i ON i.id = s.item_id
           JOIN decks d ON d.id = i.deck_id
           WHERE s.user_id=? AND s.due_at <= ? AND d.user_id=?
           ORDER BY s.due_at LIMIT 100""",
        (user["id"], now(), user["id"]),
    ).fetchall()
    return [row_to_dict(r, ("accent_json",)) for r in rows]


@router.get("/stats")
def stats(user: dict = auth.CurrentUser):
    conn = get_conn()
    uid = user["id"]
    total_attempts = conn.execute("SELECT COUNT(*) FROM attempts WHERE user_id=?", (uid,)).fetchone()[0]
    practiced_items = conn.execute("SELECT COUNT(DISTINCT item_id) FROM attempts WHERE user_id=?", (uid,)).fetchone()[0]
    avg_recent = conn.execute(
        "SELECT AVG(score) FROM (SELECT score FROM attempts WHERE user_id=? ORDER BY created_at DESC LIMIT 30)",
        (uid,),
    ).fetchone()[0]
    due_now = conn.execute("SELECT COUNT(*) FROM srs_state WHERE user_id=? AND due_at<=?", (uid, now())).fetchone()[0]
    week_ago = now() - 7 * 86400
    attempts_week = conn.execute(
        "SELECT COUNT(*) FROM attempts WHERE user_id=? AND created_at>=?", (uid, week_ago)
    ).fetchone()[0]
    return {
        "total_attempts": total_attempts,
        "practiced_items": practiced_items,
        "avg_recent_score": round(avg_recent, 1) if avg_recent else None,
        "due_now": due_now,
        "attempts_this_week": attempts_week,
    }
