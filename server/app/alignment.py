"""Approximate text↔audio alignment for the target audio.

Not forced alignment — no acoustic model. Instead:
  1. find speech chunks from frame energy (pauses split the utterance),
  2. distribute the tokenized words across the chunks' concatenated
     "speech time" proportionally to mora count, so a 2-mora particle gets
     a short span and a 5-mora verb a long one, and pauses cost nothing.

Good enough to label the contour with which word you're hearing roughly
when, which is what shadowers need to connect the diagram to the melody.
Spans are marked estimated; the frontend words them accordingly.
"""
from __future__ import annotations

import numpy as np

# frame hop is 10 ms (see config); tolerances below are in seconds
MIN_CHUNK_S = 0.06     # drop activity blips shorter than this
MERGE_GAP_S = 0.18     # gaps shorter than this join adjacent chunks


def speech_chunks(times: np.ndarray, rms: np.ndarray) -> list[tuple[float, float]]:
    """[(start, end)] of speech activity, pause-separated, in seconds."""
    if len(rms) == 0:
        return []
    floor = max(1e-4, float(np.percentile(rms, 95)) * 0.05)
    active = rms > floor
    chunks: list[list[float]] = []
    i, n = 0, len(active)
    while i < n:
        if not active[i]:
            i += 1
            continue
        j = i
        while j < n and active[j]:
            j += 1
        chunks.append([float(times[i]), float(times[j - 1])])
        i = j
    # merge across short gaps (stops/geminates inside a phrase)
    merged: list[list[float]] = []
    for c in chunks:
        if merged and c[0] - merged[-1][1] < MERGE_GAP_S:
            merged[-1][1] = c[1]
        else:
            merged.append(c)
    return [(a, b) for a, b in merged if b - a >= MIN_CHUNK_S]


def _speech_time_mapper(chunks: list[tuple[float, float]]):
    """Map cumulative speech-time (pauses excluded) -> real time."""
    starts, ends = zip(*chunks)
    durs = [e - s for s, e in chunks]
    cum = np.concatenate([[0.0], np.cumsum(durs)])
    total = float(cum[-1])

    def to_real(s: float) -> float:
        s = min(max(s, 0.0), total)
        k = int(np.searchsorted(cum, s, side="right")) - 1
        k = min(max(k, 0), len(chunks) - 1)
        return float(starts[k] + (s - cum[k]))

    return to_real, total


def align_words(times: np.ndarray, rms: np.ndarray, words: list[dict]) -> list[dict] | None:
    """Attach estimated [start, end] seconds to each word that has moras.

    `words` is the tokenizer output stored in accent_json["sentence_words"].
    Returns a list of {surface, start, end, accent, moras} or None if the
    audio has no usable speech activity.
    """
    chunks = speech_chunks(times, rms)
    if not chunks:
        return None
    to_real, total = _speech_time_mapper(chunks)
    if total <= 0:
        return None

    weights = [max(len(w.get("moras") or []), 0) for w in words]
    total_moras = sum(weights)
    if total_moras == 0:
        return None

    spans: list[dict] = []
    cursor = 0.0
    for w, wt in zip(words, weights):
        if wt == 0:
            continue
        start_s = cursor
        cursor += total * (wt / total_moras)
        spans.append({
            "surface": w["surface"],
            "start": round(to_real(start_s), 3),
            "end": round(to_real(cursor), 3),
            "accent": w.get("accent"),
            "moras": w.get("moras") or [],
        })
    return spans


def align_moras(times: np.ndarray, rms: np.ndarray, moras: list[str]) -> list[dict] | None:
    """Even mora spans over the speech portion of a single-word audio."""
    if not moras:
        return None
    chunks = speech_chunks(times, rms)
    if not chunks:
        return None
    to_real, total = _speech_time_mapper(chunks)
    if total <= 0:
        return None
    n = len(moras)
    return [{
        "surface": m,
        "start": round(to_real(total * i / n), 3),
        "end": round(to_real(total * (i + 1) / n), 3),
    } for i, m in enumerate(moras)]
