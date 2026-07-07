"""Review scheduling: SM-2 adapted to shadowing scores.

Anki users know the model: items become due, you practice them, the interval
grows when you do well. Here "quality" comes from the pronunciation score
instead of a self-graded button:

    score >= 85  -> great   (interval grows fast, ease up)
    score >= 70  -> good    (normal growth)
    score >= 55  -> pass    (slow growth)
    score <  55  -> lapse   (item comes back in 10 minutes, ease down)

Two deliberate departures from vanilla SM-2:

* State is kept per (item, user, mode) — shadowing a full sentence and
  shadowing the isolated word are different skills, so each has its own
  schedule. Word mode grows slightly faster (it's the more atomic skill).

* Practicing an item again *before* it is due does not grow the interval.
  Without this, three good takes in a row would compound 1d -> 3d -> 7d in
  five minutes. Extra practice updates the last score (and can still lapse
  the item if it goes badly); the schedule only advances at a real review.

A brand-new item becomes due immediately after its first attempt is scored.
"""
from __future__ import annotations

import random

from .db import get_conn, now, tx

DAY = 86400.0
LAPSE_RETRY_S = 600.0                         # failed items retry in 10 min
FIRST_INTERVAL = {3: 0.5, 4: 1.0, 5: 1.5}     # days after the first success
SECOND_INTERVAL = {3: 1.5, 4: 3.0, 5: 4.0}
GROWTH_TWEAK = {3: 0.75, 4: 1.0, 5: 1.15}     # quality-dependent growth factor
MODE_FACTOR = {"sentence": 1.0, "word": 1.25}


def quality_from_score(score: float) -> int:
    if score >= 85:
        return 5
    if score >= 70:
        return 4
    if score >= 55:
        return 3
    return 2


def record_result(user_id: int, item_id: int, mode: str, score: float) -> dict:
    q = quality_from_score(score)
    t = now()
    row = get_conn().execute(
        "SELECT * FROM srs_state WHERE item_id=? AND user_id=? AND mode=?",
        (item_id, user_id, mode),
    ).fetchone()
    if row:
        interval, ease, reps, lapses = row["interval_days"], row["ease"], row["reps"], row["lapses"]
        due_at = row["due_at"]
    else:
        interval, ease, reps, lapses = 0.0, 2.5, 0, 0
        due_at = t

    early = row is not None and t < due_at and reps > 0

    if q < 3:
        lapses += 1
        reps = 0
        interval = 0.0
        ease = max(1.3, ease - 0.2)
        due_at = t + LAPSE_RETRY_S
        outcome = "lapse"
    elif early:
        # extra practice between reviews: keep the schedule as it is
        outcome = "early"
    else:
        ease = max(1.3, min(2.6, ease + {3: -0.14, 4: 0.0, 5: 0.08}[q]))
        if reps == 0:
            interval = FIRST_INTERVAL[q]
        elif reps == 1:
            interval = max(interval, SECOND_INTERVAL[q])
        else:
            interval = interval * ease * GROWTH_TWEAK[q]
        interval *= MODE_FACTOR.get(mode, 1.0)
        interval *= random.uniform(0.95, 1.05)  # fuzz so items don't clump
        reps += 1
        due_at = t + interval * DAY
        outcome = "scheduled"

    with tx() as conn:
        conn.execute(
            """INSERT INTO srs_state (item_id, user_id, mode, due_at, interval_days, ease, reps, lapses, last_score)
               VALUES (?,?,?,?,?,?,?,?,?)
               ON CONFLICT(item_id, user_id, mode) DO UPDATE SET
                 due_at=excluded.due_at, interval_days=excluded.interval_days,
                 ease=excluded.ease, reps=excluded.reps, lapses=excluded.lapses,
                 last_score=excluded.last_score""",
            (item_id, user_id, mode, due_at, interval, ease, reps, lapses, score),
        )
    return {"due_at": due_at, "interval_days": round(interval, 2), "ease": round(ease, 2),
            "reps": reps, "lapses": lapses, "quality": q, "outcome": outcome}
