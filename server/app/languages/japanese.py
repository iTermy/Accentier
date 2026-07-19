"""Japanese pitch-accent module.

Target accent data comes from these tiers (best available wins, per word):
  1. the deck's curated Pitch Accent field (Kaishi's Migaku-style HTML,
     parsed by kaishi_pitch; or a numeric Yomitan PitchPosition),
  2. Yomitan pitch dictionaries / the Kanjium accent database,
  3. nothing — contour comparison against the native audio still works.

Mora segmentation follows standard rules: palatalized digraphs (キャ etc.)
are one mora; ッ, ン and long-vowel ー are separate moras.

The schematic H/L pattern for accent number n over m moras:
  n = 0 (heiban):    L H H ... H   (no drop)
  n = 1 (atamadaka): H L L ... L
  n >= 2:            L H ... H(nth) L ...  (drop right after mora n)

Sentence diagrams are built from *accent phrases*: each content word plus
its trailing particles/auxiliaries forms one phrase, the phrase accent is
derived from its words (with special handling for ます/です/ない, which
move the accent in connected speech), and successive accented phrases step
down in height (downstep). This is a rule-based approximation of Tokyo
sentence prosody — labeled as generated in the UI — but far closer to the
real melody than concatenated per-word citation patterns.
"""
from __future__ import annotations

import re
from functools import lru_cache
from typing import Any

import numpy as np

from ..alignment import align_moras
from ..dsp.yin import semitone_contour, smooth_semitones
from ..kaishi_pitch import parse_pitch_field
from ..kanjium import hira_to_kata, kata_to_hira
from ..pitchdict import lookup_accents
from .base import LanguageModule, register

SMALL_KANA = set("ャュョァィゥェォヮ")
KANA_RE = re.compile(r"[ぁ-ゖァ-ヶー]")
FURIGANA_PAIR_RE = re.compile(r" ?([^ >\[\]]+)\[([^\]]+)\]")  # anki 漢字[かんじ] markup

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


def tokenize_sentence(sentence: str, reading_overrides: dict[str, str] | None = None) -> list[dict]:
    """Tokenize and attach accent data per word where available.

    reading_overrides maps surface → kana for words whose in-context reading
    the deck knows better than unidic's default (私 = わたし not わたくし)."""
    words = []
    for w in get_tagger()(sentence):
        f = w.feature
        override = (reading_overrides or {}).get(w.surface)
        pron = override or getattr(f, "pron", None) or getattr(f, "kana", None) or w.surface
        kana = override or getattr(f, "kana", None) or pron
        if not KANA_RE.search(hira_to_kata(kana or "")) and not KANA_RE.search(w.surface):
            # punctuation / latin / digits with no kana reading
            words.append({"surface": w.surface, "moras": [], "accent": None, "pos": f.pos1})
            continue
        moras = split_moras(pron)
        lemma_kana = getattr(f, "kanaBase", None) or getattr(f, "lForm", None) or kana
        accent = None
        accents = None
        # particles/auxiliaries/suffixes are treated as accent-neutral
        # attachments — looking them up would hit homograph nouns
        # (は → 歯/葉 [1], さん → 桟 [3]) and wreck phrases
        if f.pos1 not in ("助詞", "助動詞", "接尾辞"):
            # try progressively broader keys: surface form, orthographic base,
            # lemma (kanjium indexes ある under 有る, ところ under 所, etc.)
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


# POS classes that attach to the preceding content word within an accent phrase
ATTACH_POS = {"助詞", "助動詞", "接尾辞"}
MAX_DOWNSTEP_LEVEL = 3


def _phrase_accent(group: list[dict]) -> int:
    """Accent of one accent phrase, from its words' citation accents plus
    the connected-speech behavior of the common auxiliaries."""
    total = sum(len(w["moras"]) for w in group)

    # ます/ました/ません always take the accent (帰ります → かえりま↓す)
    off = 0
    for w in group:
        if w.get("pos") == "助動詞" and w["surface"] in ("ます", "まし", "ませ"):
            return min(off + (2 if w["surface"] == "ませ" else 1), total)
        off += len(w["moras"])

    # first accented word wins; ない after it pulls the drop to just before itself
    off = 0
    first_accent: int | None = None
    for w in group:
        a = w.get("accent")
        if first_accent is None and a:
            first_accent = off + a
        if w.get("pos") == "助動詞" and w["surface"] in ("ない", "なかっ") and first_accent is not None:
            return min(off, total)
        off += len(w["moras"])
    if first_accent is not None:
        return min(first_accent, total)

    # です after an all-heiban phrase gets the accent (水です → みずで↓す)
    off = 0
    for w in group:
        if w.get("pos") == "助動詞" and w["surface"] in ("です", "でし"):
            return min(off + 1, total)
        off += len(w["moras"])
    return 0


def group_accent_phrases(words: list[dict]) -> list[dict]:
    """Merge tokenized words into accent phrases with downstep levels.

    Returns one dict per phrase: moras, accent (with ます/です/ない rules
    applied), H/L pattern, downstep level (0 = first/reset, higher = lower
    plateau), word boundaries for labeling, and break_after where punctuation
    resets the intonation."""
    groups: list[list[dict]] = []
    break_after: list[bool] = []
    cur: list[dict] | None = None
    for w in words:
        if not w.get("moras"):
            # punctuation → close the phrase and reset downstep after it
            if cur is not None:
                groups.append(cur)
                break_after.append(True)
                cur = None
            elif break_after:
                break_after[-1] = True
            continue
        attach = cur is not None and (w.get("pos") in ATTACH_POS or cur[-1].get("pos") == "接頭辞")
        if attach:
            cur.append(w)
        else:
            if cur is not None:
                groups.append(cur)
                break_after.append(False)
            cur = [w]
    if cur is not None:
        groups.append(cur)
        break_after.append(False)

    phrases: list[dict] = []
    level = 0
    for group, brk in zip(groups, break_after):
        moras: list[str] = []
        word_starts: list[dict] = []
        for w in group:
            word_starts.append({"surface": w["surface"], "at": len(moras)})
            moras.extend(w["moras"])
        accent = _phrase_accent(group)
        phrases.append({
            "surface": "".join(w["surface"] for w in group),
            "moras": moras,
            "accent": accent,
            "pattern": accent_to_pattern(len(moras), accent),
            "level": level,
            "words": word_starts,
            "break_after": brk,
        })
        if brk:
            level = 0
        elif accent > 0:
            level = min(level + 1, MAX_DOWNSTEP_LEVEL)
    return phrases


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
        reading = (note.reading or note.expression).split("・")[0].strip()
        moras = split_moras(reading)
        accent = None
        source = None
        alternates: list[int] = []

        parsed = parse_pitch_field(getattr(note, "pitch_html", "") or "")
        if parsed:
            # curated deck field (Kaishi). Prefer the alternate matching the
            # primary reading's mora count; the field's first pattern wins.
            seg = next((s for s in parsed if moras and len(s[0]) == len(moras)), parsed[0])
            if not moras:
                moras = seg[0]
            accent = seg[1]
            source = "deck"
            alternates = sorted({a for m, a in parsed if len(m) == len(seg[0])} - {seg[1]})
        elif note.pitch_position is not None:
            accent = note.pitch_position
            source = "deck"
        if accent is None:
            hit = lookup_accents(note.expression, reading)
            if hit:
                accent = hit[0][0]
                source = hit[1]

        data: dict = {
            "moras": moras,
            "accent": accent,
            "accent_source": source,
        }
        if alternates:
            data["alternates"] = alternates
        if accent is not None and moras:
            data["pattern"] = accent_to_pattern(len(moras), accent)
            data["category"] = note.pitch_categories or categorize(accent, len(moras))
        if note.sentence:
            # in-context readings: the deck's furigana + the target word itself
            overrides: dict[str, str] = {}
            for base, rd in FURIGANA_PAIR_RE.findall(getattr(note, "sentence_furigana", "") or ""):
                overrides.setdefault(base, rd)
            if reading:
                overrides[note.expression] = reading
            words = tokenize_sentence(note.sentence, overrides)
            # the curated accent for the target word beats the tokenizer's
            # dictionary lookup wherever the word appears in the sentence
            if accent is not None and moras:
                for w in words:
                    if w["surface"] == note.expression and len(w.get("moras", [])) == len(moras):
                        w["accent"] = accent
                        w["pattern"] = accent_to_pattern(len(w["moras"]), accent)
            data["sentence_words"] = words
            phrases = group_accent_phrases(words)
            if phrases:
                data["sentence_phrases"] = phrases
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
