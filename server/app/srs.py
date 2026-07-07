"""Review scheduling: SM-2 adapted to shadowing scores.

Anki users know the model: items become due, you practice them, the interval
grows when you do well. Here "quality" comes from the pronunciation score
instead of a self-graded button:

    score >= 85  -> great   (interval grows fast, ease up)
    score >= 70  -> good    (normal growth)
    score >= 55  -> pass    (slow growth)
    score <  55  -> lapse   (item comes back in 10 minutes, ease down)

A brand-new item becomes due immediately after its first attempt is scored.
"""
from __future__ import annotations

from .db import get_conn, now, tx

DAY = 86400.0


def quality_from_score(score: float) -> int:
    if score >= 85:
        return 5
    if score >= 70:
        return 4
    if score >= 55:
        return 3
    return 2


def record_result(user_id: int, item_id: int, score: float) -> dict:
    q = quality_from_score(score)
    row = get_conn().execute(
        "SELECT * FROM srs_state WHERE item_id=? AND user_id=?", (item_id, user_id)
    ).fetchone()
    if row:
        interval, ease, reps, lapses = row["interval_days"], row["ease"], row["reps"], row["lapses"]
    else:
        interval, ease, reps, lapses = 0.0, 2.5, 0, 0

    if q < 3:
        lapses += 1
        reps = 0
        interval = 0.0
        ease = max(1.3, ease - 0.2)
        due_at = now() + 600  # retry in 10 minutes
    else:
        ease = max(1.3, min(3.0, ease + {3: -0.14, 4: 0.0, 5: 0.1}[q]))
        if reps == 0:
            interval = 1.0
        elif reps == 1:
            interval = 3.0
        else:
            interval = interval * ease
        if q == 3:
            interval = max(1.0, interval * 0.75)
        reps += 1
        due_at = now() + interval * DAY

    with tx() as conn:
        conn.execute(
            """INSERT INTO srs_state (item_id, user_id, due_at, interval_days, ease, reps, lapses, last_score)
               VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT(item_id, user_id) DO UPDATE SET
                 due_at=excluded.due_at, interval_days=excluded.interval_days,
                 ease=excluded.ease, reps=excluded.reps, lapses=excluded.lapses,
                 last_score=excluded.last_score""",
            (item_id, user_id, due_at, interval, ease, reps, lapses, score),
        )
    return {"due_at": due_at, "interval_days": round(interval, 2), "ease": round(ease, 2),
            "reps": reps, "lapses": lapses, "quality": q}
