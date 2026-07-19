"""Alignment and divergence scoring between user and target F0 contours.

Pipeline:
  1. Both contours are converted to semitones relative to each speaker's own
     median voiced pitch (speaker normalization — absolute register is
     irrelevant, melody is what matters).
  2. Voiced frames are collected into (time, semitone, slope) sequences.
  3. DTW with a Sakoe-Chiba band aligns user frames to target frames.
     Cost = |Δ semitone| + 0.7 * |Δ slope| — slope term keeps rises aligned
     to rises, which matters more than absolute level for pitch accent.
  4. Metrics over the aligned pairs:
       shape      Pearson correlation of aligned semitone values
       direction  fraction of pairs whose local pitch slope agrees in sign
       level      closeness of deviation magnitude (mean |Δst| mapped to 0..1)
       timing     voiced-duration ratio between user and target
  5. Divergence regions: maximal runs of target time where |Δst| > threshold,
     labeled by what went wrong (too high / too low / direction flip).

Scores are combined with per-language weights (see LanguageModule.score_weights).
"""
from __future__ import annotations

import numpy as np

from .yin import semitone_contour, smooth_semitones

DIVERGENCE_ST = 2.8          # semitone deviation that counts as "off"
SLOPE_EPS = 8.0              # st/sec below which slope counts as flat


def _voiced_series(times: np.ndarray, st: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    smoothed = smooth_semitones(times, st)
    mask = ~np.isnan(smoothed)
    t, v = times[mask], smoothed[mask]
    slope = np.gradient(v, t) if len(v) >= 3 else np.zeros_like(v)
    return t, v, np.clip(slope, -60, 60)


def _dtw(cost: np.ndarray, band: int) -> list[tuple[int, int]]:
    n, m = cost.shape
    D = np.full((n + 1, m + 1), np.inf)
    D[0, 0] = 0.0
    for i in range(1, n + 1):
        j_lo = max(1, int(i * m / n) - band)
        j_hi = min(m, int(i * m / n) + band)
        for j in range(j_lo, j_hi + 1):
            c = cost[i - 1, j - 1]
            D[i, j] = c + min(D[i - 1, j], D[i, j - 1], D[i - 1, j - 1])
    # backtrack
    path = []
    i, j = n, m
    if not np.isfinite(D[n, m]):
        # degenerate: fall back to diagonal
        k = min(n, m)
        return [(int(i * n / k) - 1, int(i * m / k) - 1) for i in range(1, k + 1)]
    while i > 0 and j > 0:
        path.append((i - 1, j - 1))
        moves = [(D[i - 1, j - 1], i - 1, j - 1), (D[i - 1, j], i - 1, j), (D[i, j - 1], i, j - 1)]
        _, i, j = min(moves, key=lambda x: x[0])
    path.reverse()
    return path


def compare_contours(target: dict, user: dict, weights: dict[str, float]) -> dict:
    """target/user: output dicts of yin_f0. Returns metrics + visual payloads."""
    t_st, t_ref = semitone_contour(target["f0"])
    u_st, u_ref = semitone_contour(user["f0"])

    tt, tv, ts = _voiced_series(target["times"], t_st)
    ut, uv, us = _voiced_series(user["times"], u_st)

    result: dict = {
        "target_ref_hz": round(t_ref, 1),
        "user_ref_hz": round(u_ref, 1),
    }

    if len(uv) < 5 or len(tv) < 5:
        metrics = {"shape": 0.0, "direction": 0.0, "level": 0.0, "timing": 0.0,
                   "duration_ratio": 0.0, "no_voice": True}
        result.update({"metrics": metrics, "score": 0.0, "divergences": [],
                       "aligned_user": [], "user_contour": _pack(ut, uv), "warp": []})
        return result

    # cost matrix: level + slope-direction agreement
    cost = np.abs(uv[:, None] - tv[None, :]) + 0.7 * np.abs(us[:, None] - ts[None, :]) / 20.0
    band = max(12, int(0.15 * max(len(uv), len(tv))))
    path = _dtw(cost, band)

    ui = np.array([p[0] for p in path])
    ti = np.array([p[1] for p in path])
    dev = uv[ui] - tv[ti]

    # DTW can flatter a wrong take: with enough local stretching almost any
    # melody correlates with the target. Diagonality measures how much of the
    # path advances both series together; plateaus (one frame absorbing many)
    # mean the "match" only exists after heavy warping, so shape/direction
    # get discounted accordingly.
    steps = np.diff(np.stack([ui, ti], axis=1), axis=0)
    diag_steps = int(np.sum((steps[:, 0] > 0) & (steps[:, 1] > 0)))
    max_diag = max(1, min(len(uv), len(tv)) - 1)
    diagonality = min(1.0, diag_steps / max_diag)
    warp_discount = 0.75 + 0.25 * diagonality

    # ---- metrics ----
    if np.std(uv[ui]) > 1e-6 and np.std(tv[ti]) > 1e-6:
        shape = float(np.corrcoef(uv[ui], tv[ti])[0, 1])
    else:
        shape = 0.0
    shape_score = max(0.0, shape) * warp_discount

    su, stg = us[ui], ts[ti]
    both_flat = (np.abs(su) < SLOPE_EPS) & (np.abs(stg) < SLOPE_EPS)
    same_sign = np.sign(su) == np.sign(stg)
    direction = float(np.mean(both_flat | same_sign)) * warp_discount

    mean_abs_dev = float(np.mean(np.abs(dev)))
    level = float(np.clip(1.0 - max(0.0, mean_abs_dev - 0.8) / 4.0, 0.0, 1.0))

    dur_u = ut[-1] - ut[0]
    dur_t = tt[-1] - tt[0]
    duration_ratio = dur_u / dur_t if dur_t > 0 else 0.0
    timing = float(np.clip(min(dur_u, dur_t) / max(dur_u, dur_t), 0.0, 1.0)) if dur_u > 0 else 0.0

    metrics = {
        "shape": round(shape_score, 3),
        "direction": round(direction, 3),
        "level": round(level, 3),
        "timing": round(timing, 3),
        "duration_ratio": round(duration_ratio, 3),
        "mean_abs_dev_st": round(mean_abs_dev, 2),
        "warp": round(diagonality, 3),
    }
    score = 100.0 * sum(weights[k] * metrics[k] for k in ("shape", "direction", "level", "timing"))

    # ---- divergence regions on the target timeline ----
    divergences = []
    run_start = None
    run_devs: list[float] = []
    for k in range(len(path)):
        off = abs(dev[k]) > DIVERGENCE_ST
        if off and run_start is None:
            run_start = tt[ti[k]]
            run_devs = [dev[k]]
        elif off:
            run_devs.append(dev[k])
        elif run_start is not None:
            end = tt[ti[k]]
            if end - run_start >= 0.09:  # ignore blips < 90 ms
                mean_d = float(np.mean(run_devs))
                divergences.append({
                    "start": round(float(run_start), 3),
                    "end": round(float(end), 3),
                    "kind": "too_high" if mean_d > 0 else "too_low",
                    "mean_dev_st": round(mean_d, 2),
                })
            run_start, run_devs = None, []
    if run_start is not None and tt[ti[-1]] - run_start >= 0.09:
        mean_d = float(np.mean(run_devs))
        divergences.append({"start": round(float(run_start), 3), "end": round(float(tt[ti[-1]]), 3),
                            "kind": "too_high" if mean_d > 0 else "too_low",
                            "mean_dev_st": round(mean_d, 2)})

    # user contour warped onto the target timeline for direct overlay
    aligned_user = _pack(tt[ti], uv[ui])

    # sparse user_time -> target_time mapping so the frontend can move a
    # playhead over the overlay chart while the user's own take plays back
    warp = [[round(float(ut[ui[k]]), 3), round(float(tt[ti[k]]), 3)]
            for k in range(0, len(path), 3)]

    result.update({
        "metrics": metrics,
        "score": round(score, 1),
        "divergences": divergences,
        "aligned_user": aligned_user,
        "user_contour": _pack(ut, uv),
        "warp": warp,
    })
    return result


def pack_contour(times: np.ndarray, f0_st: np.ndarray) -> list:
    """Full contour incl. gaps (null = unvoiced) for drawing the target."""
    out = []
    for t, v in zip(times, f0_st):
        out.append([round(float(t), 3), None if np.isnan(v) else round(float(v), 2)])
    return out


def _pack(times: np.ndarray, values: np.ndarray) -> list:
    return [[round(float(t), 3), round(float(v), 2)] for t, v in zip(times, values)]
