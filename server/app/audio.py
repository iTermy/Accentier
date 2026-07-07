"""Audio decode + resample. Everything downstream works on mono float32 @ 16 kHz."""
from __future__ import annotations

import io
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.signal import resample_poly

from .config import ANALYSIS_SR


def decode_audio(source: bytes | str | Path, target_sr: int = ANALYSIS_SR) -> tuple[np.ndarray, int]:
    """Decode any libsndfile-supported audio (wav/mp3/flac/ogg) to mono float32 at target_sr."""
    if isinstance(source, bytes):
        data, sr = sf.read(io.BytesIO(source), dtype="float32", always_2d=True)
    else:
        data, sr = sf.read(str(source), dtype="float32", always_2d=True)
    mono = data.mean(axis=1)
    if sr != target_sr:
        g = np.gcd(sr, target_sr)
        mono = resample_poly(mono, target_sr // g, sr // g).astype(np.float32)
    # normalize peak to avoid level-dependent behavior downstream
    peak = np.abs(mono).max()
    if peak > 1e-6:
        mono = mono / peak * 0.95
    return mono.astype(np.float32), target_sr


def save_wav(path: str | Path, samples: np.ndarray, sr: int = ANALYSIS_SR) -> None:
    sf.write(str(path), samples, sr, subtype="PCM_16")
