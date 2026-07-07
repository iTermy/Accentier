"""Generic (contour-only) language module.

Used for any language without a dedicated module (e.g. the Italian deck).
No accent dictionary or segmentation — the target is purely the native
audio's F0 contour, and scoring leans harder on overall shape/melody.
This is also the extension point blueprint: a future `english` module would
add stressed-syllable detection here, `mandarin` would add tone categories.
"""
from __future__ import annotations

from typing import Any

from .base import LanguageModule, register


class GenericModule(LanguageModule):
    id = "generic"
    name = "Generic (intonation contour)"

    def build_accent_data(self, note: Any) -> dict:
        return {"moras": [], "accent": None, "accent_source": None}

    def score_weights(self) -> dict[str, float]:
        return {"shape": 0.45, "direction": 0.25, "level": 0.10, "timing": 0.20}

    def feedback_notes(self, metrics: dict, accent: dict | None) -> list[str]:
        notes: list[str] = []
        if metrics.get("no_voice"):
            return ["No voiced speech detected — check your microphone and try again."]
        if metrics["shape"] < 0.4:
            notes.append("Your melody diverges from the native speaker — listen again and mimic the rises and falls.")
        if metrics["direction"] < 0.6:
            notes.append("Intonation moves in the wrong direction in places — match where the voice goes up and down.")
        if metrics["timing"] < 0.7:
            notes.append("Rhythm is off — try to match the native speaker's pace.")
        if not notes:
            notes.append("Good match — your intonation closely follows the native audio.")
        return notes


register(GenericModule())
