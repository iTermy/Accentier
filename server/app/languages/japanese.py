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

import numpy as np

from ..alignment import align_moras
from ..dsp.yin import semitone_contour, smooth_semitones
from ..kanjium import hira_to_kata, kata_to_hira
from ..pitchdict import lookup_accents
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
                hit = lookup_accents(surf, kata_to_hira(rd or ""))
                if hit:
                    accents = hit[0]
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


def estimate_accent(analysis: dict, moras: list[str]) -> int | None:
    """Guess the accent number by reading the word audio's own melody.

    Split the speech portion into even mora bins, take each bin's mean
    pitch, and look for the downstep. Caveats inherent to the method:
    heiban [0] and odaka [n] are indistinguishable for a word in isolation
    (the odaka drop happens on a following particle), so "no drop" maps to
    0. Results are labeled as estimates in the UI.
    """
    n = len(moras)
    if n == 0:
        return None
    st, _ = semitone_contour(analysis["f0"])
    sm = smooth_semitones(analysis["times"], st)
    spans = align_moras(analysis["times"], analysis["rms"], moras, analysis.get("f0"))
    if not spans:
        return None
    times = analysis["times"]
    means: list[float] = []
    for sp in spans:
        mask = (times >= sp["start"]) & (times <= sp["end"]) & ~np.isnan(sm)
        means.append(float(np.mean(sm[mask])) if int(mask.sum()) >= 2 else np.nan)

    known = [(i, m) for i, m in enumerate(means) if not np.isnan(m)]
    if len(known) < max(2, (n + 1) // 2):
        # a one-mora word: judge by the fall within the vowel
        if n == 1 and known:
            voiced = sm[~np.isnan(sm)]
            if len(voiced) >= 4:
                return 1 if voiced[: len(voiced) // 3].mean() - voiced[-len(voiced) // 3:].mean() > 2.0 else 0
        return None

    # largest downstep between consecutive measured moras
    drop_after: int | None = None
    drop_size = 0.0
    for (i, a), (j, b) in zip(known, known[1:]):
        step = b - a
        if step < drop_size:
            drop_size = step
            drop_after = i + 1  # accent number is 1-indexed mora before the drop
    if drop_after is not None and drop_size <= -1.5:
        return min(drop_after, n)
    return 0


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
            hit = lookup_accents(note.expression, reading)
            if hit:
                accent = hit[0][0]
                source = hit[1]
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

    def estimate_accent_from_audio(self, analysis: dict, accent: dict) -> dict | None:
        moras = accent.get("moras") or []
        est = estimate_accent(analysis, moras)
        if est is None:
            return None
        return {
            "accent": est,
            "accent_source": "audio",
            "pattern": accent_to_pattern(len(moras), est),
            "category": categorize(est, len(moras)),
        }

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
        if metrics.get("warp", 1.0) < 0.55:
            notes.append("Your melody only lines up after heavy time-stretching — try to hit the rises and falls at the same rhythm as the native audio.")
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
