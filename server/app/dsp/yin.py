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


def _postprocess(f0: np.ndarray, max_jump_semitones: float = 6.0) -> np.ndarray:
    """Median-smooth voiced runs and drop isolated blips / octave spikes."""
    out = f0.copy()
    # 5-point median filter over voiced neighborhoods
    for i in range(len(out)):
        if np.isnan(out[i]):
            continue
        lo, hi = max(0, i - 2), min(len(out), i + 3)
        window = out[lo:hi]
        vals = window[~np.isnan(window)]
        if len(vals) >= 3:
            out[i] = np.median(vals)
    # remove voiced runs shorter than 3 frames (30 ms) — usually artifacts
    i = 0
    n = len(out)
    while i < n:
        if np.isnan(out[i]):
            i += 1
            continue
        j = i
        while j < n and not np.isnan(out[j]):
            j += 1
        if j - i < 3:
            out[i:j] = np.nan
        i = j
    # kill single-frame octave jumps inside runs
    for i in range(1, n - 1):
        if np.isnan(out[i]) or np.isnan(out[i - 1]) or np.isnan(out[i + 1]):
            continue
        jump_prev = abs(12 * np.log2(out[i] / out[i - 1]))
        jump_next = abs(12 * np.log2(out[i] / out[i + 1]))
        neighbors_close = abs(12 * np.log2(out[i + 1] / out[i - 1])) < 2.0
        if jump_prev > max_jump_semitones and jump_next > max_jump_semitones and neighbors_close:
            out[i] = (out[i - 1] + out[i + 1]) / 2
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
