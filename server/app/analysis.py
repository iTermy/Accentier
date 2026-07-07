"""Orchestrates DSP + language modules for target and attempt analysis."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from .audio import decode_audio
from .config import MEDIA_DIR
from .db import get_conn, tx
from .dsp.align import compare_contours, pack_contour
from .dsp.yin import semitone_contour, yin_f0
from .languages.base import get_module


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
    if mode in cached:
        return cached[mode]

    filename = item["sentence_audio"] if mode == "sentence" else item["word_audio"]
    if not filename:
        return None
    analysis = _analyze_file(media_path(item["deck_id"], filename))
    if analysis is None:
        return None
    st, ref = semitone_contour(analysis["f0"])
    payload = {
        "contour": pack_contour(analysis["times"], st),
        "ref_hz": round(ref, 1),
        "duration": round(analysis["duration"], 3),
    }
    cached[mode] = payload
    with tx() as conn:
        conn.execute("UPDATE items SET target_json=? WHERE id=?", (json.dumps(cached), item["id"]))
    return payload


def analyze_attempt(item: dict, language: str, user_audio: bytes, mode: str) -> dict:
    """Run the full comparison for a user recording against the item's target."""
    filename = item["sentence_audio"] if mode == "sentence" else item["word_audio"]
    target_raw = _analyze_file(media_path(item["deck_id"], filename))
    if target_raw is None:
        raise ValueError("Target audio missing for this item")

    samples, sr = decode_audio(user_audio)
    user_raw = {**yin_f0(samples, sr), "duration": len(samples) / sr}

    module = get_module(language)
    comparison = compare_contours(target_raw, user_raw, module.score_weights())
    comparison["notes"] = module.feedback_notes(comparison["metrics"], item.get("accent"))
    comparison["mode"] = mode
    comparison["user_duration"] = round(user_raw["duration"], 3)
    return comparison
