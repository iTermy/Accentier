"""YIN fundamental-frequency estimation (de Cheveigné & Kawahara, 2002).

Implemented from the paper in vectorized NumPy:
  1. difference function d(tau) computed for all frames at once via
     FFT autocorrelation + cumulative energy terms,
  2. cumulative mean normalized difference function (CMNDF),
  3. absolute-threshold dip selection with local-minimum refinement,
  4. parabolic interpolation of the selected lag,
  5. voicing decision from CMNDF depth + frame energy,
  6. median smoothing and short-run pruning to kill octave glitches.

Returns times, f0 (Hz, NaN where unvoiced) and a per-frame confidence.
"""
from __future__ import annotations

import numpy as np

from ..config import ANALYSIS_SR, F0_FRAME, F0_HOP, F0_MAX, F0_MIN


def _frame_signal(x: np.ndarray, frame_length: int, hop: int) -> np.ndarray:
    n_frames = max(1, 1 + (len(x) - frame_length) // hop) if len(x) >= frame_length else 0
    if n_frames == 0:
        x = np.pad(x, (0, frame_length - len(x)))
        n_frames = 1
    idx = np.arange(frame_length)[None, :] + hop * np.arange(n_frames)[:, None]
    return x[idx]


def _difference_function(frames: np.ndarray, tau_max: int) -> np.ndarray:
    """d[f, tau] = sum_{j=0}^{W-tau-1} (x_j - x_{j+tau})^2, vectorized via FFT ACF."""
    n_frames, w = frames.shape
    fft_size = 1 << int(np.ceil(np.log2(2 * w)))
    spec = np.fft.rfft(frames, fft_size, axis=1)
    acf = np.fft.irfft(spec * np.conj(spec), fft_size, axis=1)[:, : tau_max + 1]

    sq = frames**2
    # c[f, k] = sum of first k squared samples
    c = np.concatenate([np.zeros((n_frames, 1)), np.cumsum(sq, axis=1)], axis=1)
    taus = np.arange(tau_max + 1)
    term1 = c[:, w - taus]                    # energy of x[0 : W-tau]
    term2 = c[:, [w]] - c[:, taus]            # energy of x[tau : W]
    d = term1 + term2 - 2 * acf
    return np.maximum(d, 0.0)


def _cmndf(d: np.ndarray) -> np.ndarray:
    tau = np.arange(1, d.shape[1])
    cumsum = np.cumsum(d[:, 1:], axis=1)
    cmndf = np.ones_like(d)
    with np.errstate(divide="ignore", invalid="ignore"):
        cmndf[:, 1:] = d[:, 1:] * tau[None, :] / np.where(cumsum > 0, cumsum, np.inf)
    return cmndf


def yin_f0(
    x: np.ndarray,
    sr: int = ANALYSIS_SR,
    frame_length: int = F0_FRAME,
    hop: int = F0_HOP,
    fmin: float = F0_MIN,
    fmax: float = F0_MAX,
    threshold: float = 0.15,
    voicing_threshold: float = 0.35,
) -> dict:
    """Track F0. Returns dict with times, f0 (NaN = unvoiced), confidence, rms."""
    x = np.asarray(x, dtype=np.float64)
    tau_min = max(2, int(sr / fmax))
    tau_max = int(np.ceil(sr / fmin))
    assert tau_max < frame_length, "frame too short for fmin"

    frames = _frame_signal(x, frame_length, hop)
    n_frames = frames.shape[0]
    rms = np.sqrt((frames**2).mean(axis=1))
    d = _difference_function(frames, tau_max)
    cm = _cmndf(d)

    f0 = np.full(n_frames, np.nan)
    conf = np.zeros(n_frames)

    # silence gate: frames far below the utterance's active level can't carry pitch
    active = rms > max(1e-4, np.percentile(rms, 90) * 0.02)

    region = cm[:, tau_min : tau_max + 1]
    below = region < threshold
    for i in range(n_frames):
        if not active[i]:
            continue
        row = region[i]
        if below[i].any():
            tau = int(np.argmax(below[i]))  # first dip under threshold
            # descend to the local minimum of this dip
            while tau + 1 < len(row) and row[tau + 1] < row[tau]:
                tau += 1
        else:
            tau = int(np.argmin(row))
        depth = row[tau]
        tau_abs = tau + tau_min
        # parabolic interpolation around the minimum
        if 0 < tau_abs < tau_max:
            a, b, c = cm[i, tau_abs - 1], cm[i, tau_abs], cm[i, tau_abs + 1]
            denom = a - 2 * b + c
            shift = 0.5 * (a - c) / denom if abs(denom) > 1e-12 else 0.0
            shift = np.clip(shift, -1, 1)
        else:
            shift = 0.0
        if depth < voicing_threshold:
            f0[i] = sr / (tau_abs + shift)
            conf[i] = 1.0 - depth

    f0 = _postprocess(f0)
    times = (np.arange(n_frames) * hop + frame_length / 2) / sr
    return {"times": times, "f0": f0, "confidence": conf, "rms": rms}


def _voiced_runs(x: np.ndarray) -> list[tuple[int, int]]:
    """[start, end) index pairs of contiguous voiced (non-NaN) frames."""
    runs = []
    i, n = 0, len(x)
    while i < n:
        if np.isnan(x[i]):
            i += 1
            continue
        j = i
        while j < n and not np.isnan(x[j]):
            j += 1
        runs.append((i, j))
        i = j
    return runs


def _postprocess(f0: np.ndarray, max_jump_semitones: float = 6.0) -> np.ndarray:
    """Repair the raw track: median-smooth, prune blips, fix octave errors.

    Octave (halving/doubling) errors are THE noise source in speech F0 —
    creaky voice and low-energy frames make YIN lock onto a subharmonic for
    a stretch of frames, which shows up as a sudden ±12 st cliff in the
    contour. We fix them at the segment level, not just single frames:
    voiced runs are split wherever consecutive frames jump > 9 st, and any
    resulting segment sitting ≈ an octave away from the utterance's median
    pitch is shifted back.
    """
    out = f0.copy()
    n = len(out)
    # 5-point median filter over voiced neighborhoods
    for i in range(n):
        if np.isnan(out[i]):
            continue
        lo, hi = max(0, i - 2), min(n, i + 3)
        window = out[lo:hi]
        vals = window[~np.isnan(window)]
        if len(vals) >= 3:
            out[i] = np.median(vals)
    # remove voiced runs shorter than 3 frames (30 ms) — usually artifacts
    for i, j in _voiced_runs(out):
        if j - i < 3:
            out[i:j] = np.nan

    # segment-level octave correction: split runs at big jumps, then compare
    # each segment's median to the global median
    voiced = out[~np.isnan(out)]
    if len(voiced) >= 5:
        global_med = np.median(np.log2(voiced))
        segments: list[tuple[int, int]] = []
        for i, j in _voiced_runs(out):
            s = i
            for k in range(i + 1, j):
                if abs(12 * np.log2(out[k] / out[k - 1])) > 9.0:
                    segments.append((s, k))
                    s = k
            segments.append((s, j))
        for s, e in segments:
            if e - s == 0 or (e - s) > 80:  # leave long stable stretches alone
                continue
            seg_med = np.median(np.log2(out[s:e]))
            dev_st = 12 * (seg_med - global_med)
            for octaves in (1, -1):
                if abs(dev_st - 12 * octaves) < 4.0 and abs(dev_st) > 8.0:
                    out[s:e] = out[s:e] / (2.0 ** octaves)
                    break

    # kill remaining single-frame spikes inside runs
    for i in range(1, n - 1):
        if np.isnan(out[i]) or np.isnan(out[i - 1]) or np.isnan(out[i + 1]):
            continue
        jump_prev = abs(12 * np.log2(out[i] / out[i - 1]))
        jump_next = abs(12 * np.log2(out[i] / out[i + 1]))
        neighbors_close = abs(12 * np.log2(out[i + 1] / out[i - 1])) < 2.0
        if jump_prev > max_jump_semitones and jump_next > max_jump_semitones and neighbors_close:
            out[i] = (out[i - 1] + out[i + 1]) / 2
    return out


def smooth_semitones(
    times: np.ndarray,
    st: np.ndarray,
    bridge_s: float = 0.12,
    window: int = 9,
) -> np.ndarray:
    """Clean a semitone track for humans to read.

    Two steps, both gentle enough to keep accent drops (100–200 ms events)
    intact while killing frame-level jitter:
      1. bridge unvoiced gaps shorter than `bridge_s` by linear interpolation
         (obstruents inside a word break the track; melodically the line
         continues through them),
      2. Savitzky-Golay (quadratic) smoothing per voiced segment.
    NaN is preserved for real pauses.
    """
    from scipy.signal import savgol_filter

    out = st.astype(float).copy()
    n = len(out)
    if n < 3:
        return out
    hop = float(np.median(np.diff(times))) if n > 1 else 0.01
    max_gap = max(1, int(round(bridge_s / hop)))

    # 1. bridge short gaps between voiced neighbors
    runs = _voiced_runs(out)
    for (a_start, a_end), (b_start, _) in zip(runs, runs[1:]):
        gap = b_start - a_end
        if 0 < gap <= max_gap:
            left, right = out[a_end - 1], out[b_start]
            out[a_end:b_start] = np.interp(
                np.arange(a_end, b_start), [a_end - 1, b_start], [left, right]
            )

    # 2. smooth each voiced segment independently
    for i, j in _voiced_runs(out):
        seg = out[i:j]
        if len(seg) >= 5:
            w = min(window, len(seg) if len(seg) % 2 == 1 else len(seg) - 1)
            if w >= 5:
                out[i:j] = savgol_filter(seg, w, polyorder=2)
    return out


def semitone_contour(f0: np.ndarray) -> tuple[np.ndarray, float]:
    """Convert Hz to semitones relative to the speaker's median voiced pitch.

    This is the core speaker normalization: a 90 Hz male voice and a 250 Hz
    female voice shadowing the same sentence produce comparable contours.
    """
    voiced = f0[~np.isnan(f0)]
    if len(voiced) == 0:
        return np.full_like(f0, np.nan), 0.0
    ref = float(np.median(voiced))
    with np.errstate(invalid="ignore", divide="ignore"):
        st = 12.0 * np.log2(f0 / ref)
    return st, ref
