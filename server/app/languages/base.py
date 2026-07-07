"""Language module interface.

A language module owns everything language-specific:
  - how a practice item's *target pattern* is derived (accent dictionaries,
    mora/syllable segmentation, tokenization),
  - what schematic diagram the frontend should draw,
  - how the contour comparison is scored and worded.

The DSP layer (F0 extraction, DTW alignment) is language-neutral and lives in
app.dsp; modules consume its output and attach language-specific meaning.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class LanguageModule(ABC):
    id: str
    name: str

    @abstractmethod
    def build_accent_data(self, note: Any) -> dict:
        """Derive target accent/pattern data for a parsed note at import time.
        Returns a JSON-serializable dict stored on the item (accent_json)."""

    @abstractmethod
    def score_weights(self) -> dict[str, float]:
        """Relative weights of the alignment subscores for this language."""

    def feedback_notes(self, metrics: dict, accent: dict | None) -> list[str]:
        """Human-readable coaching notes derived from alignment metrics."""
        return []

    def estimate_accent_from_audio(self, analysis: dict, accent: dict) -> dict | None:
        """Best-effort accent guess from the target audio's own F0 track,
        for items the dictionaries don't cover. `analysis` is yin_f0 output.
        Return accent_json field updates (accent/pattern/category/...) or
        None if the audio is inconclusive."""
        return None


_REGISTRY: dict[str, LanguageModule] = {}


def register(module: LanguageModule) -> None:
    _REGISTRY[module.id] = module


def get_module(lang: str) -> LanguageModule:
    return _REGISTRY.get(lang) or _REGISTRY["generic"]


def detect_language(sample_texts: list[str]) -> str:
    """Cheap script-based detection over a sample of note texts."""
    jp = total = 0
    for t in sample_texts:
        for ch in t:
            if "぀" <= ch <= "ヿ" or "一" <= ch <= "鿿":
                jp += 1
            if not ch.isspace():
                total += 1
    if total and jp / total > 0.15:
        return "ja"
    return "generic"
