"""Japanese pitch-accent module.

Target accent data comes from three tiers (best available wins, per word):
  1. the deck's own Yomitan PitchPosition field,
  2. the Kanjium accent database (surface + reading lookup),
  3. nothing — contour comparison against the native audio still works.

Mora segmentation follows standard rules: palatalized digraphs (キャ etc.)
are one mora; ッ, ン and long-vowel ー are separate moras.

The schematic H/L pattern for accent number n over m moras:
  n = 0 (heiban):    L H H ... H   (no drop)
  n = 1 (atamadaka): H L L ... L
  n >= 2:            L H ... H(nth) L ...  (drop right after mora n)

Sentence diagrams concatenate per-word patterns from the tokenizer. This
ignores cross-word accent-phrase merging and downstep — good enough to show
*where the drops are*, which is what shadowers need. Documented limitation.
"""
from __future__ import annotations

import re
from functools import lru_cache
from typing import Any

from ..kanjium import get_kanjium, hira_to_kata, kata_to_hira
from .base import LanguageModule, register

SMALL_KANA = set("ャュョァィゥェォヮ")
KANA_RE = re.compile(r"[ぁ-ゖァ-ヶー]")

CONTENT_POS = {"名詞", "動詞", "形容詞", "副詞", "形状詞", "感動詞", "代名詞", "接頭辞", "連体詞"}


@lru_cache(maxsize=1)
def get_tagger():
    import fugashi
    return fugashi.Tagger()


def split_moras(kana: str) -> list[str]:
    """Split katakana/hiragana string into moras."""
    kata = hira_to_kata(kana)
    moras: list[str] = []
    for ch in kata:
        if ch in SMALL_KANA and moras:
            moras[-1] += ch
        elif KANA_RE.match(ch) or ch == "ー":
            moras.append(ch)
        # non-kana characters (punctuation, latin) are dropped
    return moras


def accent_to_pattern(n_moras: int, accent: int) -> list[int]:
    """1 = high, 0 = low, one entry per mora."""
    if n_moras == 0:
        return []
    if accent == 0:
        return [0] + [1] * (n_moras - 1)
    if accent == 1:
        return [1] + [0] * (n_moras - 1)
    accent = min(accent, n_moras)
    return [0] + [1] * (accent - 1) + [0] * (n_moras - accent)


def categorize(accent: int, n_moras: int) -> str:
    if accent == 0:
        return "heiban"
    if accent == 1:
        return "atamadaka"
    if accent >= n_moras:
        return "odaka"
    return "nakadaka"


def tokenize_sentence(sentence: str) -> list[dict]:
    """Tokenize and attach accent data per word where available."""
    kanjium = get_kanjium()
    words = []
    for w in get_tagger()(sentence):
        f = w.feature
        pron = getattr(f, "pron", None) or getattr(f, "kana", None) or w.surface
        kana = getattr(f, "kana", None) or pron
        if not KANA_RE.search(hira_to_kata(kana or "")) and not KANA_RE.search(w.surface):
            # punctuation / latin / digits with no kana reading
            words.append({"surface": w.surface, "moras": [], "accent": None, "pos": f.pos1})
            continue
        moras = split_moras(pron)
        lemma_kana = getattr(f, "kanaBase", None) or getattr(f, "lForm", None) or kana
        accent = None
        accents = None
        # try progressively broader keys: surface form, orthographic base, lemma
        # (kanjium indexes ある under 有る, ところ under 所, etc.)
        for surf, rd in (
            (w.surface, kana),
            (getattr(f, "orthBase", None), lemma_kana),
            (getattr(f, "lemma", None), lemma_kana),
        ):
            if surf:
                accents = kanjium.lookup(surf, kata_to_hira(rd or ""))
                if accents:
                    break
        if accents:
            accent = accents[0]
        entry = {
            "surface": w.surface,
            "kana": kana,
            "moras": moras,
            "accent": accent,
            "pos": f.pos1,
            "content": f.pos1 in CONTENT_POS,
        }
        if accent is not None and moras:
            entry["pattern"] = accent_to_pattern(len(moras), accent)
        words.append(entry)
    return words


class JapaneseModule(LanguageModule):
    id = "ja"
    name = "Japanese (pitch accent)"

    def build_accent_data(self, note: Any) -> dict:
        reading = note.reading or note.expression
        moras = split_moras(reading)
        accent = None
        source = None
        if note.pitch_position is not None:
            accent = note.pitch_position
            source = "deck"
        else:
            accents = get_kanjium().lookup(note.expression, reading)
            if accents:
                accent = accents[0]
                source = "kanjium"
        data: dict = {
            "moras": moras,
            "accent": accent,
            "accent_source": source,
        }
        if accent is not None and moras:
            data["pattern"] = accent_to_pattern(len(moras), accent)
            data["category"] = note.pitch_categories or categorize(accent, len(moras))
        if note.sentence:
            data["sentence_words"] = tokenize_sentence(note.sentence)
        return data

    def score_weights(self) -> dict[str, float]:
        # Pitch *shape* and drop placement dominate for Japanese.
        return {"shape": 0.35, "direction": 0.30, "level": 0.20, "timing": 0.15}

    def feedback_notes(self, metrics: dict, accent: dict | None) -> list[str]:
        notes: list[str] = []
        if metrics.get("no_voice"):
            return ["No voiced speech detected — check your microphone and try again."]
        if metrics["direction"] < 0.55:
            notes.append("Your pitch rises and falls in different places than the target — focus on where the accent drops.")
        elif metrics["direction"] < 0.75:
            notes.append("Pitch movement is close but some rises/falls are misplaced. Listen for the drop position.")
        if metrics["shape"] < 0.4:
            notes.append("The overall contour shape diverges from the native audio — try exaggerating the target's melody.")
        if metrics["level"] < 0.5:
            notes.append("Your pitch range differs a lot from the target — Japanese accents are drops, not big swings; keep highs and lows closer.")
        if metrics["timing"] < 0.7:
            ratio = metrics.get("duration_ratio", 1.0)
            notes.append("You spoke noticeably {} than the target — shadow at native speed.".format(
                "slower" if ratio > 1 else "faster"))
        if accent and accent.get("accent") is not None and not notes:
            cat = accent.get("category", "")
            if accent["accent"] == 0:
                notes.append(f"Good match. Target is {cat} [0] — flat pattern, keep it level with no drop.")
            else:
                notes.append(f"Good match. Target is {cat} [{accent['accent']}] — keep that drop crisp.")
        elif not notes:
            notes.append("Good match — contour closely follows the native audio.")
        return notes


register(JapaneseModule())
