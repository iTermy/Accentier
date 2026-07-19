"""Orchestrates DSP + language modules for target and attempt analysis."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from .alignment import align_moras, align_words
from .audio import decode_audio
from .config import MEDIA_DIR
from .db import get_conn, tx
from .dsp.align import compare_contours, pack_contour
from .dsp.yin import semitone_contour, smooth_semitones, yin_f0
from .languages.base import get_module

# bump when the target payload format/DSP changes; stale caches regenerate
TARGET_VERSION = 4


def media_path(deck_id: int, filename: str) -> Path:
    return MEDIA_DIR / str(deck_id) / filename


def _analyze_file(path: Path) -> dict | None:
    if not path.exists():
        return None
    samples, sr = decode_audio(path)
    return {**yin_f0(samples, sr), "duration": len(samples) / sr}


def get_target_analysis(item: dict, mode: str = "sentence") -> dict | None:
    """Compute (or load cached) target contour for an item + mode."""
    cached = item.get("target") or {}
    if mode in cached and cached[mode].get("v") == TARGET_VERSION:
        return cached[mode]

    filename = item["sentence_audio"] if mode == "sentence" else item["word_audio"]
    if not filename:
        return None
    analysis = _analyze_file(media_path(item["deck_id"], filename))
    if analysis is None:
        return None
    st, ref = semitone_contour(analysis["f0"])
    display = smooth_semitones(analysis["times"], st)
    payload = {
        "v": TARGET_VERSION,
        "contour": pack_contour(analysis["times"], display),
        "ref_hz": round(ref, 1),
        "duration": round(analysis["duration"], 3),
    }

    # estimated word/mora time spans so the chart can label the melody
    accent = item.get("accent") or {}
    if mode == "sentence" and accent.get("sentence_words"):
        spans = align_words(analysis["times"], analysis["rms"],
                            accent["sentence_words"], analysis["f0"])
        if spans:
            payload["words"] = spans
    elif mode == "word" and accent.get("moras"):
        spans = align_moras(analysis["times"], analysis["rms"],
                            accent["moras"], analysis["f0"])
        if spans:
            payload["moras"] = spans

    cached[mode] = payload
    with tx() as conn:
        conn.execute("UPDATE items SET target_json=? WHERE id=?", (json.dumps(cached), item["id"]))
    return payload


def ensure_accent_estimate(item: dict, language: str) -> dict | None:
    """For items without dictionary accent data, try estimating the accent
    from the word audio itself (cached in accent_json; negative results too,
    so the audio is only analyzed once). Returns the updated accent dict."""
    accent = item.get("accent")
    if accent is None or accent.get("accent") is not None or accent.get("estimate_failed"):
        return accent
    if not item.get("word_audio") or not accent.get("moras"):
        return accent

    module = get_module(language)
    analysis = _analyze_file(media_path(item["deck_id"], item["word_audio"]))
    updates = module.estimate_accent_from_audio(analysis, accent) if analysis else None
    if updates is None:
        accent["estimate_failed"] = True
    else:
        accent.update(updates)
    with tx() as conn:
        conn.execute("UPDATE items SET accent_json=? WHERE id=?",
                     (json.dumps(accent, ensure_ascii=False), item["id"]))
    return accent


def analyze_attempt(item: dict, language: str, user_audio: bytes, mode: str,
                    slice_range: tuple[float, float] | None = None) -> dict:
    """Run the full comparison for a user recording against the item's target.

    slice_range=(start, end) compares against just that window of the target
    (the frontend's region selection). Frame times stay absolute, so the
    returned divergences/aligned contour land on the right part of the chart.
    """
    filename = item["sentence_audio"] if mode == "sentence" else item["word_audio"]
    target_raw = _analyze_file(media_path(item["deck_id"], filename))
    if target_raw is None:
        raise ValueError("Target audio missing for this item")

    if slice_range is not None:
        start = max(0.0, slice_range[0])
        end = min(target_raw["duration"], slice_range[1])
        if end - start < 0.25:
            raise ValueError("Selected region is too short to analyze")
        mask = (target_raw["times"] >= start) & (target_raw["times"] <= end)
        if int(mask.sum()) < 5:
            raise ValueError("Selected region is too short to analyze")
        target_raw = {
            **{k: v[mask] for k, v in target_raw.items() if isinstance(v, np.ndarray)},
            "duration": end - start,
        }

    samples, sr = decode_audio(user_audio)
    user_raw = {**yin_f0(samples, sr), "duration": len(samples) / sr}

    module = get_module(language)
    comparison = compare_contours(target_raw, user_raw, module.score_weights())
    comparison["notes"] = module.feedback_notes(comparison["metrics"], item.get("accent"))
    comparison["mode"] = mode
    comparison["user_duration"] = round(user_raw["duration"], 3)
    if slice_range is not None:
        comparison["slice"] = [round(start, 3), round(end, 3)]
    return comparison
