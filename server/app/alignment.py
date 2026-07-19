"""Approximate text↔audio alignment for the target audio.

Not forced alignment — no acoustic model. Instead:
  1. find speech chunks from frame energy (pauses split the utterance), with
     the noise floor raised for recordings whose quietest frames are still
     loud (music/ambience beds under TV and anime lines), then refine with
     the F0 track: chunks with (almost) no voiced frames are not speech
     (breaths, clicks, music-only stretches) and chunk edges are tightened
     to the voiced span, so leading noise doesn't shift every label,
  2. partition the word sequence across the chunks with a small dynamic
     program matching each chunk's share of time to each word run's share
     of moras — splits prefer punctuation but don't require it, so pauses
     without commas and commas without pauses both come out right, and a
     non-speech chunk that slipped through can absorb zero words,
  3. distribute words inside a chunk proportionally to mora weight, where
     devoiceable moras (キ/ク/シ/ス/チ/ツ/ヒ/フ/ピ/プ and their ュ forms
     before a voiceless onset or utterance-finally) count short, matching
     how they compress in natural speech,
  4. snap each word boundary to the best nearby boundary evidence — an RMS
     dip, preferably an unvoiced one (a consonant closure).

Good enough to label the contour with which word you're hearing roughly
when, which is what shadowers need to connect the diagram to the melody.
Spans are marked estimated; the frontend words them accordingly.
"""
from __future__ import annotations

import numpy as np

# frame hop is 10 ms (see config); tolerances below are in seconds
MIN_CHUNK_S = 0.06     # drop activity blips shorter than this
MERGE_GAP_S = 0.18     # gaps shorter than this join adjacent chunks
SNAP_RADIUS_S = 0.09   # how far a word boundary may move to reach an energy dip
VOICED_LEAD_S = 0.12   # voiceless-onset allowance when tightening chunk starts
VOICED_TAIL_S = 0.08   # release allowance when tightening chunk ends
SPLIT_PENALTY = 0.03   # DP cost of splitting words at a non-punctuation boundary

PAUSE_PUNCT = set("、。．，！？!?,.…‥・「」『』（）()")

# devoicing: high vowels /i u/ between voiceless consonants (or utterance-
# finally, like the す of です/ます) lose their voicing and most of their
# length. Weighting them short keeps every later word from drifting right.
VOICELESS_ONSET = set("カキクケコサシスセソタチツテトハヒフヘホパピプペポッ")
DEVOICEABLE = {"キ", "ク", "シ", "ス", "チ", "ツ", "ヒ", "フ", "ピ", "プ",
               "キュ", "シュ", "チュ", "ヒュ", "ピュ"}


def speech_chunks(times: np.ndarray, rms: np.ndarray,
                  f0: np.ndarray | None = None) -> list[tuple[float, float]]:
    """[(start, end)] of speech activity, pause-separated, in seconds.

    When the f0 track is provided, it is used as speech evidence: pitchless
    chunks are dropped and chunk edges are pulled in to the voiced span.
    """
    if len(rms) == 0:
        return []
    hop = float(np.median(np.diff(times))) if len(times) > 1 else 0.01
    p95 = float(np.percentile(rms, 95))
    # noise floor: 8% of the peak level, raised when even the quietest frames
    # are loud — that's a background bed, and speech must beat it, not silence
    bed = float(np.percentile(rms, 5))
    floor = max(1e-4, 0.08 * p95, min(1.8 * bed, 0.2 * p95))
    active = rms > floor
    raw: list[list[int]] = []
    i, n = 0, len(active)
    while i < n:
        if not active[i]:
            i += 1
            continue
        j = i
        while j < n and active[j]:
            j += 1
        raw.append([i, j])
        i = j
    # merge across short gaps (stops/geminates inside a phrase)
    merged: list[list[int]] = []
    for c in raw:
        if merged and float(times[c[0]] - times[merged[-1][1] - 1]) < MERGE_GAP_S:
            merged[-1][1] = c[1]
        else:
            merged.append(c)
    merged = [c for c in merged if float(times[c[1] - 1] - times[c[0]]) >= MIN_CHUNK_S]

    if f0 is not None and len(f0) == len(rms):
        voiced = ~np.isnan(np.asarray(f0))
        lead = max(1, int(round(VOICED_LEAD_S / hop)))
        tail = max(1, int(round(VOICED_TAIL_S / hop)))
        refined: list[list[int]] = []
        for i, j in merged:
            vi = np.nonzero(voiced[i:j])[0]
            # a chunk that is barely voiced carries no pitch to label —
            # breath, click, or music-only stretch, not speech
            if len(vi) < max(3, 0.15 * (j - i)):
                continue
            i2 = max(i, i + int(vi[0]) - lead)
            j2 = min(j, i + int(vi[-1]) + 1 + tail)
            if float(times[j2 - 1] - times[i2]) >= MIN_CHUNK_S:
                refined.append([i2, j2])
        merged = refined

    # weak edge chunks are breaths / clicks / background beds, not speech
    while len(merged) > 1 and float(rms[merged[0][0]:merged[0][1]].max()) < 0.2 * p95:
        merged.pop(0)
    while len(merged) > 1 and float(rms[merged[-1][0]:merged[-1][1]].max()) < 0.2 * p95:
        merged.pop()
    return [(float(times[i]), float(times[j - 1])) for i, j in merged]


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


def _mora_weights(moras: list[str]) -> list[float]:
    """Duration weight per mora; devoiceable moras count short."""
    out = []
    for k, m in enumerate(moras):
        nxt = moras[k + 1] if k + 1 < len(moras) else None
        devoiced = m in DEVOICEABLE and (nxt is None or nxt[0] in VOICELESS_ONSET)
        out.append(0.6 if devoiced else 1.0)
    return out


def _word_weights(words: list[dict]) -> list[float]:
    """Length weight per word: devoicing-aware mora weights, with lookahead
    across word boundaries (the devoicing trigger is often the next word's
    first consonant)."""
    flat: list[str] = []
    owner: list[int] = []
    for k, w in enumerate(words):
        for m in w.get("moras") or []:
            flat.append(m)
            owner.append(k)
    weights = [0.0] * len(words)
    for wt, k in zip(_mora_weights(flat), owner):
        weights[k] += wt
    return weights


CAPTION_OPEN = set("（(")
CAPTION_CLOSE = set("）)")


def _content_words(words: list[dict]) -> tuple[list[dict], list[bool]]:
    """Words that carry moras, plus punct_after[i] = True when pause
    punctuation follows kept word i in the original sequence.

    Words inside （…） are dropped: in mined subtitle text these are speaker
    names / sound captions that are never spoken — assigning them audio
    shifts every real word's label. If the whole sentence is bracketed
    (or brackets are unbalanced enough to eat everything), fall back to
    keeping all words.
    """
    kept: list[dict] = []
    punct_after: list[bool] = []
    depth = 0
    for w in words:
        surface = w.get("surface") or ""
        depth += sum(ch in CAPTION_OPEN for ch in surface)
        if w.get("moras"):
            if depth == 0:
                kept.append(w)
                punct_after.append(False)
        elif kept and set(surface) & PAUSE_PUNCT:
            punct_after[-1] = True
        depth = max(0, depth - sum(ch in CAPTION_CLOSE for ch in surface))
    if not kept:
        kept = [w for w in words if w.get("moras")]
        punct_after = [False] * len(kept)
    return kept, punct_after


def _partition(weights: list[float], punct_after: list[bool],
               chunk_durs: list[float]) -> list[tuple[int, int]]:
    """Split the word sequence into len(chunk_durs) contiguous (possibly
    empty) runs so each chunk's share of speech time matches its words'
    share of total mora weight. Splitting between words that aren't
    separated by punctuation costs a little extra; a chunk may take zero
    words (it was probably not speech). O(chunks * words²) — both tiny.
    """
    n, k = len(weights), len(chunk_durs)
    total_w = sum(weights) or 1.0
    total_d = sum(chunk_durs) or 1.0
    cum = [0.0]
    for w in weights:
        cum.append(cum[-1] + w)
    inf = float("inf")
    best = [[inf] * (n + 1) for _ in range(k + 1)]
    prev = [[0] * (n + 1) for _ in range(k + 1)]
    best[0][0] = 0.0
    for c in range(1, k + 1):
        share_d = chunk_durs[c - 1] / total_d
        for i in range(n + 1):
            for j in range(i + 1):
                if best[c - 1][j] == inf:
                    continue
                cost = best[c - 1][j] + ((cum[i] - cum[j]) / total_w - share_d) ** 2
                if i > j and 0 < j < n and not punct_after[j - 1]:
                    cost += SPLIT_PENALTY
                if cost < best[c][i]:
                    best[c][i] = cost
                    prev[c][i] = j
    cuts = [n]
    for c in range(k, 0, -1):
        cuts.append(prev[c][cuts[-1]])
    cuts.reverse()
    return [(cuts[c], cuts[c + 1]) for c in range(k)]


def _snap_boundaries(bounds: list[float], times: np.ndarray, rms: np.ndarray,
                     f0: np.ndarray | None = None) -> list[float]:
    """Nudge interior boundaries to the best nearby boundary evidence.
    Word transitions usually coincide with a consonant closure — an RMS
    valley that is ideally also unvoiced."""
    p95 = float(np.percentile(rms, 95)) or 1.0
    score = rms / p95
    if f0 is not None and len(f0) == len(rms):
        score = score + 0.5 * (~np.isnan(np.asarray(f0))).astype(float)
    out = list(bounds)
    for k in range(1, len(out) - 1):
        lo = max(out[k - 1] + 0.02, out[k] - SNAP_RADIUS_S)
        hi = min(out[k + 1] - 0.02, out[k] + SNAP_RADIUS_S)
        if hi <= lo:
            continue
        idx = np.nonzero((times >= lo) & (times <= hi))[0]
        if len(idx) < 2:
            continue
        out[k] = float(times[idx[np.argmin(score[idx])]])
    return out


def _distribute(words: list[dict], weights: list[float], start: float, end: float,
                times: np.ndarray, rms: np.ndarray,
                f0: np.ndarray | None = None) -> list[dict] | None:
    """Weight-proportional spans over one chunk, dip-snapped."""
    total_w = sum(weights)
    if total_w <= 0 or end <= start:
        return None
    bounds = [start]
    acc = 0.0
    for wt in weights:
        acc += wt
        bounds.append(start + (end - start) * (acc / total_w))
    bounds = _snap_boundaries(bounds, times, rms, f0)
    return [{
        "surface": w["surface"],
        "start": round(bounds[k], 3),
        "end": round(bounds[k + 1], 3),
        "accent": w.get("accent"),
        "moras": w.get("moras") or [],
    } for k, w in enumerate(words)]


def align_words(times: np.ndarray, rms: np.ndarray, words: list[dict],
                f0: np.ndarray | None = None) -> list[dict] | None:
    """Attach estimated [start, end] seconds to each word that has moras.

    `words` is the tokenizer output stored in accent_json["sentence_words"].
    Returns a list of {surface, start, end, accent, moras} or None if the
    audio has no usable speech activity.
    """
    chunks = speech_chunks(times, rms, f0)
    if not chunks:
        return None
    kept, punct_after = _content_words(words)
    if not kept:
        return None
    weights = _word_weights(kept)
    if sum(weights) <= 0:
        return None
    groups = _partition(weights, punct_after, [e - s for s, e in chunks])
    spans: list[dict] = []
    for (a, b), (start, end) in zip(groups, chunks):
        if a == b:
            continue  # this chunk absorbed no words (probably not speech)
        part = _distribute(kept[a:b], weights[a:b], start, end, times, rms, f0)
        if part:
            spans.extend(part)
    return spans or None


def align_moras(times: np.ndarray, rms: np.ndarray, moras: list[str],
                f0: np.ndarray | None = None) -> list[dict] | None:
    """Weighted mora spans over the speech portion of a single-word audio."""
    if not moras:
        return None
    chunks = speech_chunks(times, rms, f0)
    if not chunks:
        return None
    to_real, total = _speech_time_mapper(chunks)
    if total <= 0:
        return None
    weights = _mora_weights(moras)
    total_w = sum(weights) or 1.0
    out: list[dict] = []
    acc = 0.0
    for m, wt in zip(moras, weights):
        start = to_real(total * acc / total_w)
        acc += wt
        out.append({
            "surface": m,
            "start": round(start, 3),
            "end": round(to_real(total * acc / total_w), 3),
        })
    return out
