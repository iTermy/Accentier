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


DEFECTIVE_MORAS = {"ッ", "ン", "ー"}
# 連体詞 that fuse with the following noun into one accent phrase (このひ↓と)
DEMONSTRATIVES = {"この", "その", "あの", "どの", "こんな", "そんな", "あんな", "どんな"}
# moras that devoice between voiceless consonants (hint generation only)
DEVOICEABLE = set("キクシスチツヒフピ")
VOICELESS_ONSET = set("カキクケコサシスセソタチツテトハヒフヘホパピプペポ")
# attached 形状詞 whose accent unidic-lite is missing
OWN_ACCENT_FALLBACK = {"みたい": 2, "よう": 1, "そう": 1}

_ACON_CLAUSE_RE = re.compile(r"(名詞|動詞|形容詞)%(F\d)(?:@(-?\d+))?")


def _first_atype(raw) -> int | None:
    """unidic aType is '2', '0,3', or '*' — take the first (NHK-primary)."""
    s = "" if raw is None else str(raw)
    head = s.split(",")[0].strip()
    try:
        return int(head)
    except ValueError:
        return None


def _acon_rule(spec: str | None, headcat: str) -> tuple[str, int] | None:
    """Pick the aConType clause for the phrase head's category (名詞 fallback)."""
    if not spec or spec == "*":
        return None
    clauses = {cat: (rule, int(val) if val else 0)
               for cat, rule, val in _ACON_CLAUSE_RE.findall(spec)}
    return clauses.get(headcat) or clauses.get("名詞")


def _loanword_accent(moras: list[str]) -> int:
    """Default katakana-name/loanword accent: fall after the antepenultimate
    mora, retreating off defective moras (ト↓ム, コンピュ↓ーター)."""
    n = len(moras)
    if n <= 2:
        return 1
    a = n - 2
    while a > 1 and moras[a - 1] in DEFECTIVE_MORAS:
        a -= 1
    return a


KATAKANA_ONLY_RE = re.compile(r"^[ァ-ヶー]+$")

# accents curated by the loaded deck, keyed (surface, hiragana-reading) —
# populated at seed time so every sentence renders a word with the same
# accent the deck's own word diagram shows (deck wins over NHK ordering)
_CURATED_ACCENTS: dict[tuple[str, str], int] = {}


def set_curated_accents(mapping: dict[tuple[str, str], int]) -> None:
    _CURATED_ACCENTS.clear()
    _CURATED_ACCENTS.update(mapping)


def _curated_or_dict_accent(surf: str, rd: str) -> int | None:
    key = (surf, kata_to_hira(rd or ""))
    if key in _CURATED_ACCENTS:
        return _CURATED_ACCENTS[key]
    hit = lookup_accents(surf, key[1])
    return hit[0][0] if hit else None


# two letters minimum: single A/B in dialogue are speaker labels, not words
ASCII_LETTERS_RE = re.compile(r"^[A-Za-zＡ-Ｚａ-ｚ]{2,5}$")
LETTER_PRON = {
    "a": "エー", "b": "ビー", "c": "シー", "d": "ディー", "e": "イー",
    "f": "エフ", "g": "ジー", "h": "エイチ", "i": "アイ", "j": "ジェー",
    "k": "ケー", "l": "エル", "m": "エム", "n": "エヌ", "o": "オー",
    "p": "ピー", "q": "キュー", "r": "アール", "s": "エス", "t": "ティー",
    "u": "ユー", "v": "ブイ", "w": "ダブリュー", "x": "エックス",
    "y": "ワイ", "z": "ゼット",
}


def _acronym_pron_accent(surface: str) -> tuple[str, int] | None:
    """CD → (シーディー, 3): letter-name reading, accent on the first mora of
    the last letter (エヌエイチケ↓ー pattern)."""
    if not ASCII_LETTERS_RE.match(surface):
        return None
    letters = [LETTER_PRON.get(ch.lower()) for ch in
               surface.translate(str.maketrans("ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ",
                                               "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"))]
    if not all(letters):
        return None
    pron = "".join(letters)
    accent = len(split_moras(pron)) - len(split_moras(letters[-1])) + 1
    return pron, accent


_O_COL = set("おこそとのほもよろごぞどぼぽょォオコソトノホモヨロゴゾドボポ")
_E_COL = set("えけせてねへめれげぜでべぺェエケセテネヘメレゲゼデベペ")


def _canon_reading(s: str) -> str:
    """Normalize for comparison: katakana→hiragana and おう/えい → おー/えー
    (unidic prons write long vowels as ー, furigana writes them as kana)."""
    s = kata_to_hira(hira_to_kata(s or ""))
    out: list[str] = []
    for ch in s:
        if out and ((ch == "う" and out[-1] in _O_COL) or (ch == "い" and out[-1] in _E_COL)):
            out.append("ー")
        else:
            out.append(ch)
    return "".join(out)


def _resolve_override(surface: str, pron: str | None,
                      overrides: dict[str, list[str]] | None) -> str | None:
    """Pick the deck-furigana reading for a token, or None to keep unidic's.

    A kanji can carry several readings across one sentence (旅行[りょこう]に
    行[い]きました), so overrides are per-base lists and unidic's own reading
    wins whenever it agrees with ANY candidate — the override only kicks in
    when unidic picked a reading the furigana contradicts (私 → わたし,
    何時 → なんじ, 気に入り → いり)."""
    if not overrides:
        return None
    cands = list(overrides.get(surface) or [])
    trail = LEADING_KANA_RE.search(surface[::-1])
    k = len(trail.group(0)) if trail else 0
    if 0 < k < len(surface):
        for rd in overrides.get(surface[:-k]) or []:
            cands.append(rd + surface[-k:])
    if not cands and len(surface) >= 2:
        # per-character composition (何時 = 何[なん]+時[じ]); a kanji can carry
        # several readings across the sentence (分[わ]かる vs 自分[じぶん]),
        # so try every combination — unidic agreement below picks the winner
        per_char = [overrides.get(ch) or [] for ch in surface]
        if all(per_char):
            combos = [""]
            for rds in per_char:
                combos = [c + rd for c in combos for rd in rds][:8]
            cands.extend(combos)
    if not cands:
        return None
    p = _canon_reading(pron or "")
    for c in cands:
        if _canon_reading(c) == p:
            return None
    return cands[0]


NUM_READINGS = {"0": "ゼロ", "1": "イチ", "2": "ニ", "3": "サン", "4": "ヨン",
                "5": "ゴ", "6": "ロク", "7": "ナナ", "8": "ハチ", "9": "キュウ"}


def _number_pron(digits: str) -> str:
    """27 → ニジュウナナ. unidic-lite has no readings for ASCII-digit tokens."""
    try:
        v = int(digits)
    except ValueError:
        return ""
    if v == 0:
        return "ゼロ"
    if v >= 10000:
        return ""  # 万+ appear as separate tokens in practice
    out = ""
    for unit, name in ((1000, "セン"), (100, "ヒャク"), (10, "ジュウ")):
        d, v = divmod(v, unit)
        if d:
            out += (NUM_READINGS[str(d)] if d > 1 else "") + name
    if v:
        out += NUM_READINGS[str(v)]
    return out


def tokenize_sentence(sentence: str, reading_overrides: dict[str, list[str]] | None = None) -> list[dict]:
    """Tokenize and attach accent data per word where available.

    reading_overrides maps furigana base → candidate kana readings for words
    whose in-context reading the deck knows better than unidic's default
    (私 = わたし not わたくし); see _resolve_override.

    Per-token accents: conjugated verb/adjective tokens use unidic's aType,
    which is form-specific (帰っ=1, 分かっ=2) — applying a dictionary's
    citation accent to a conjugated surface would misplace the fall.
    Dictionary-form tokens and plain nouns prefer the pitch dictionaries
    (better curated), falling back to aType, then to the loanword default
    for out-of-vocabulary katakana."""
    words = []
    for w in get_tagger()(sentence):
        f = w.feature
        raw_pron = getattr(f, "pron", None) or getattr(f, "kana", None)
        override = _resolve_override(w.surface, raw_pron, reading_overrides)
        acronym = None if override or raw_pron else _acronym_pron_accent(w.surface)
        if acronym:
            raw_pron = acronym[0]
        pron = override or raw_pron or _number_pron(w.surface) or w.surface
        kana = override or getattr(f, "kana", None) or pron
        if not KANA_RE.search(hira_to_kata(kana or "")) and not KANA_RE.search(w.surface):
            # punctuation / latin / digits with no kana reading
            words.append({"surface": w.surface, "moras": [], "accent": None, "pos": f.pos1})
            continue
        moras = split_moras(pron)
        lemma_kana = getattr(f, "kanaBase", None) or getattr(f, "lForm", None) or kana
        feat = {
            "pos2": getattr(f, "pos2", None),
            "cType": getattr(f, "cType", None),
            "cForm": getattr(f, "cForm", None),
            "aType": getattr(f, "aType", None),
            "aConType": getattr(f, "aConType", None),
            "aModeType": getattr(f, "aModeType", None),
            "orthBase": getattr(f, "orthBase", None),
            "lemma": getattr(f, "lemma", None),
        }
        atype = _first_atype(feat["aType"])
        conjugating = bool(feat["cType"]) and feat["cType"] != "*"
        dict_form = conjugating and str(feat["cForm"] or "").startswith(("終止形", "連体形"))
        accent = None
        # particles/auxiliaries/suffixes are accent-neutral attachments —
        # looking them up would hit homograph nouns (は → 歯/葉 [1]); the
        # phrase walker applies their combination rules instead. 助動詞語幹
        # そう/よう/みたい likewise: a lookup would hit the homograph adverb
        # そう [0] — the walker's own-accent table has the right values.
        if f.pos1 not in ("助詞", "助動詞", "接尾辞") \
                and not (f.pos1 == "形状詞" and getattr(f, "pos2", None) == "助動詞語幹"):
            if conjugating and not dict_form:
                accent = atype
                # aModeType self-modification for conjugated forms:
                # M1@u = fall u moras from the end, always (行こう → いこ↓う);
                # M2@u = same but only for otherwise-flat forms (赤かっ(た) →
                # あか↓かった; accented 高かっ keeps its aType)
                mode = str(feat["aModeType"] or "")
                mm = re.match(r"M([12])@(\d+)", mode)
                if mm and moras:
                    if mm.group(1) == "1" or not accent:
                        accent = max(len(moras) - int(mm.group(2)), 1)
            else:
                # progressively broader keys: surface, orthographic base,
                # lemma (kanjium indexes ある under 有る, ところ under 所)
                keys = [
                    (w.surface, kana),
                    (_digits_to_kanji(w.surface), kana),
                    (feat["orthBase"], lemma_kana),
                    (feat["lemma"], lemma_kana),
                ]
                # the deck's curated accent wins on ANY key before the
                # dictionaries answer on any (沢山 curates [0]; NHK's first
                # listing for the surface たくさん is [3])
                for surf, rd in keys:
                    if surf:
                        accent = _CURATED_ACCENTS.get((surf, kata_to_hira(rd or "")))
                        if accent is not None:
                            break
                if accent is None:
                    for surf, rd in keys:
                        if surf:
                            hit = lookup_accents(surf, kata_to_hira(rd or ""))
                            if hit:
                                accent = hit[0][0]
                                break
                if accent is None:
                    accent = atype
                if accent is None and acronym:
                    accent = acronym[1]
                if accent is None and moras and KATAKANA_ONLY_RE.match(hira_to_kata(w.surface)):
                    accent = _loanword_accent(moras)
        entry = {
            "surface": w.surface,
            "kana": kana,
            "moras": moras,
            "accent": accent,
            "pos": f.pos1,
            "content": f.pos1 in CONTENT_POS,
            "feat": feat,
        }
        if accent is not None and moras:
            entry["pattern"] = accent_to_pattern(len(moras), accent)
        words.append(entry)
    return words


DIGIT_KANJI = {"0": "〇", "1": "一", "2": "二", "3": "三", "4": "四",
               "5": "五", "6": "六", "7": "七", "8": "八", "9": "九"}


def _digits_to_kanji(s: str) -> str:
    """1時 → 一時, 30分 → 三十分 — so digit surfaces hit dictionary keys."""
    def conv(m: re.Match) -> str:
        v = int(m.group(0))
        if v == 0:
            return "〇"
        out = ""
        for unit, char in ((1000, "千"), (100, "百"), (10, "十")):
            d, v = divmod(v, unit)
            if d:
                out += (DIGIT_KANJI[str(d)] if d > 1 else "") + char
        if v:
            out += DIGIT_KANJI[str(v)]
        return out
    return re.sub(r"\d+", conv, s)


_H_TO_P = str.maketrans("はひふへほ", "ぱぴぷぺぽ")
_H_TO_B = str.maketrans("はひふへほ", "ばびぶべぼ")


def _counter_reading_variants(num: str, rest: str) -> list[str]:
    """Sound-change variants for number+counter readings (both hiragana):
    いち+ふん → いっぷん, さん+ふん → さんぷん — so fused dictionary lookups
    hit even though the concatenated token readings don't."""
    if not num or not rest:
        return []
    out = []
    first = rest[0]
    if num[-1] in "ちくう" and len(num) > 1:
        stem = num[:-1] + "っ"
        if first in "かきくけこさしすせそたちつてと":
            out.append(stem + rest)
        elif first in "はひふへほ":
            out.append(stem + rest.translate(_H_TO_P))
    if num[-1] == "ん" and first in "はひふへほ":
        out.append(num + rest.translate(_H_TO_P))
        out.append(num + rest.translate(_H_TO_B))
    return out


FUSABLE_POS = {"名詞", "接頭辞", "接尾辞"}


def fuse_dictionary_runs(words: list[dict]) -> list[dict]:
    """Fuse consecutive noun-ish tokens that the pitch dictionaries know as a
    single word (日本+語 → 日本語 [0], 一+つ → 一つ [2]). unidic splits many
    lexicalized compounds whose accent is not derivable from the parts."""
    out: list[dict] = []
    i = 0
    while i < len(words):
        w = words[i]
        # scale words extend the number itself (6+万 → one 数詞 6万), keeping
        # pos2 数詞 so a following counter still applies (6万+円 → ろくまんえ↓ん)
        if (w.get("feat") or {}).get("pos2") == "数詞" and w.get("moras") \
                and i + 1 < len(words) and words[i + 1]["surface"] in ("万", "億", "兆") \
                and words[i + 1].get("moras"):
            sc = words[i + 1]
            w = {
                "surface": w["surface"] + sc["surface"],
                "kana": hira_to_kata(w["kana"]) + hira_to_kata(sc["kana"]),
                "moras": w["moras"] + sc["moras"],
                "accent": None,
                "pos": "名詞",
                "content": True,
                "feat": {"fused": True, "pos2": "数詞"},
            }
            words = words[:i] + [w] + words[i + 2:]
        best = None
        if w.get("pos") in FUSABLE_POS and w.get("moras"):
            limit = min(i + 4, len(words))
            # prefix-initiated runs may span verb tokens: 相+変わら+ず is the
            # lexicalized adverb 相変わらず, listed whole in the dictionaries
            cont_pos = FUSABLE_POS | ({"動詞", "助動詞"} if w["pos"] == "接頭辞" else set())
            for j in range(limit, i + 1, -1):
                run = words[i:j]
                if len(run) < 2 or not all(r.get("moras") for r in run) or not all(
                    r.get("pos") in (FUSABLE_POS if k == 0 else cont_pos)
                    for k, r in enumerate(run)
                ):
                    continue
                surf = "".join(r["surface"] for r in run)
                rd = kata_to_hira("".join(hira_to_kata(r["kana"]) for r in run))
                candidates = [rd]
                if run[0].get("feat", {}).get("pos2") == "数詞" and len(run) == 2:
                    num_rd = kata_to_hira(hira_to_kata(run[0]["kana"]))
                    rest_rd = kata_to_hira(hira_to_kata(run[1]["kana"]))
                    # sound-changed readings first: いっさい is the real
                    # reading of 一歳 even when a dict lists いちさい too
                    candidates = _counter_reading_variants(num_rd, rest_rd) + candidates
                acc = rd2 = None
                for cand in candidates:
                    acc = _curated_or_dict_accent(_digits_to_kanji(surf), cand)
                    if acc is not None:
                        rd2 = cand
                        break
                if acc is not None:
                    best = (j, run, surf, rd2, acc)
                    break
        if best is None and w.get("feat", {}).get("pos2") == "数詞" and w.get("moras") \
                and i + 1 < len(words) and words[i + 1]["surface"] in COUNTER_ACCENT \
                and words[i + 1].get("moras"):
            # no dictionary entry: fuse number+counter with the standard sound
            # changes (いち+さい → いっさい) and the counter's accent rule
            cnt = words[i + 1]
            num_rd = kata_to_hira(hira_to_kata(w["kana"]))
            cnt_rd = kata_to_hira(hira_to_kata(cnt["kana"]))
            variants = _counter_reading_variants(num_rd, cnt_rd)
            rd = variants[0] if variants else num_rd + cnt_rd
            moras = split_moras(rd)
            n_num = len(w["moras"])
            if COUNTER_ACCENT[cnt["surface"]] == "pre":
                acc = n_num
                while acc > 1 and moras[acc - 1] in DEFECTIVE_MORAS | {"ウ", "イ"}:
                    acc -= 1
            else:
                acc = min(n_num + 1, len(moras))
            best = (i + 2, [w, cnt], w["surface"] + cnt["surface"], rd, acc)
        if best:
            j, run, surf, rd, accent = best
            moras = split_moras(rd)
            out.append({
                "surface": surf,
                "kana": rd,
                "moras": moras,
                "accent": accent,
                "pos": "名詞",
                "content": True,
                "pattern": accent_to_pattern(len(moras), accent),
                "feat": {"fused": True},
            })
            i = j
        else:
            out.append(w)
            i += 1
    return out


# POS classes that attach to the preceding content word within an accent phrase
ATTACH_POS = {"助詞", "助動詞", "接尾辞"}
MAX_DOWNSTEP_LEVEL = 3

# counter accent fallback when no dictionary entry covers the fused number:
# "pre" = fall on the number's last full mora (さ↓んじ, さんじゅ↓っぷん),
# "post" = fall right after the junction (さんに↓ん, ひゃくえ↓ん)
COUNTER_ACCENT = {
    "時": "pre", "歳": "pre", "才": "pre", "分": "pre", "度": "pre",
    "個": "pre", "冊": "pre", "匹": "pre", "本": "pre", "杯": "pre",
    "回": "pre", "枚": "pre", "台": "pre",
    "人": "post", "円": "post", "年": "post", "時間": "post", "問": "post",
    "週間": "post", "ヶ月": "post", "か月": "post", "カ月": "post", "年間": "post",
    "ページ": "post", "メートル": "post", "グラム": "post", "パーセント": "post",
    "キロ": "post", "センチ": "post", "ドル": "post", "ポンド": "post", "ユーロ": "post",
}

# frequent second elements of compound verbs (attach to a deverbal noun first
# element that unidic tags 名詞: 跳び+出す)
COMPOUND_V2 = {"出す", "始める", "続ける", "終える", "終わる", "かける", "掛ける",
               "合う", "込む", "回る", "過ぎる", "直す", "換える", "替える", "切る"}

# particles that gain their own fall after an unaccented host (みずま↓で)
SELF_ACCENTING_PARTICLES = {
    "まで": 1, "など": 1, "ばかり": 1, "ぐらい": 1, "くらい": 1,
    "より": 1, "なんて": 1, "かしら": 1, "さえ": 1, "すら": 1, "こそ": 1,
}


def _retract(a: int, pmoras: list[str]) -> int:
    """Move a fall one mora back, skipping defective moras (た↓べて, み↓て)."""
    a -= 1
    while a > 1 and pmoras[a - 1] in DEFECTIVE_MORAS:
        a -= 1
    return max(a, 1)


def _phrase_accent(group: list[dict]) -> tuple[int, list[dict]]:
    """Accent of one accent phrase via the unidic accent-combination rules
    (F1–F4 attachment, M1/M2 self-modification, C-rules for suffixes) plus
    the engine overrides documented in docs/ja_sentence_pitch_accent.md.

    Returns (accent, events) — events record which rules fired, for hints."""
    events: list[dict] = []
    a = 0            # fall position so far (0 = flat)
    a_src = ""       # what established the fall: "verb" chain or "noun" host —
                     # ます/ない/られる only manipulate falls of their own verb
                     # chain (勉強しま↓す) and never steal a noun's fall
                     # (か↓んしゃします keeps 感謝's accent)
    n = 0            # moras so far
    pmoras: list[str] = []
    headcat = "名詞"  # aConType clause selector: 名詞 / 動詞 / 形容詞
    prev: dict | None = None
    prev_conj = False  # does the chain end in a conjugating stem?

    def is_verbal(w: dict) -> bool:
        feat = w.get("feat") or {}
        return w.get("pos") in ("動詞", "形容詞") or bool(feat.get("cType") and feat["cType"] != "*")

    for w in group:
        m = len(w["moras"])
        if m == 0:
            continue
        feat = w.get("feat") or {}
        pos = w.get("pos")
        pos2 = feat.get("pos2") or ""
        ctype = feat.get("cType") or ""
        surface = w["surface"]
        own = w.get("accent")
        if own is None:
            own = OWN_ACCENT_FALLBACK.get(surface)

        if n == 0:
            # phrase head
            a = own or 0
            a_src = "verb" if pos in ("動詞", "形容詞") else "noun"
            if pos == "動詞":
                headcat = "動詞"
            elif pos == "形容詞":
                headcat = "形容詞"
        elif pos not in ATTACH_POS:
            # attached content word: merged demonstrative+noun, サ変 noun+する,
            # 補助動詞 after て, attached 形状詞 (そう/よう/みたい), 補助形容詞 ない
            crule = str(feat.get("aConType") or "")
            counter = COUNTER_ACCENT.get(surface) \
                if prev is not None and (prev.get("feat") or {}).get("pos2") == "数詞" else None
            if counter == "pre":
                a = n
                while a > 1 and pmoras[a - 1] in DEFECTIVE_MORAS | {"ウ", "イ"}:
                    a -= 1
                a_src = "noun"
            elif counter == "post":
                a = n + 1
                a_src = "noun"
            elif own:
                if crule.startswith("C1") and pos in ("形状詞", "名詞"):
                    old = a
                    a = n + own          # attachment's own accent wins (げんきそ↓う)
                    a_src = "verb" if pos in ("動詞", "形容詞") else "noun"
                    if old and old != a:
                        events.append({"kind": "c1_replace", "surface": surface})
                elif a == 0:
                    a = n + own          # このひ↓と, いってくださ↓い, じゃな↓い
                    a_src = "verb" if pos in ("動詞", "形容詞") else "noun"
            if pos == "動詞":
                headcat = "動詞"
                # a compound verb's chain belongs to the verb (跳び出しま↓した:
                # ます may steal) — but する/できる after a サ変 noun leaves the
                # noun's accent in charge (か↓んしゃします)
                if feat.get("orthBase") not in ("する", "為る", "できる", "出来る") \
                        and prev is not None and prev.get("pos") in ("名詞", "接尾辞"):
                    a_src = "verb"
            elif pos == "形容詞":
                headcat = "形容詞"
        else:
            # ---- clitic attachment (助詞 / 助動詞 / 接尾辞) ----
            was = a
            if pos == "接尾辞":
                crule = str(feat.get("aConType") or "")
                counter = COUNTER_ACCENT.get(surface) \
                    if prev is not None and (prev.get("feat") or {}).get("pos2") == "数詞" else None
                if counter == "pre":
                    a = n
                    # the fall retreats off defective moras and trailing
                    # vowel-extension moras (さんじゅう → さんじゅ↓っぷん)
                    while a > 1 and pmoras[a - 1] in DEFECTIVE_MORAS | {"ウ", "イ"}:
                        a -= 1
                elif counter == "post":
                    a = n + 1
                elif crule.startswith("C1") and own:
                    a = n + own
                elif crule.startswith(("C2", "C3")) and a == 0 and own:
                    a = n + own
                # C4/C5 (さん, たち): ride the host
            elif ctype == "助動詞-タ" or (not feat and pos == "助動詞" and surface in ("た", "たら", "だ")):
                # no odaka fall materializes after flat hosts (買った [0]);
                # a fall on the chain-final stem mora retracts (た↓べた)
                if a > 0 and a == n and prev_conj:
                    a = _retract(a, pmoras)
                    events.append({"kind": "ta_retract", "from": was, "to": a})
            elif pos == "助詞" and pos2 == "接続助詞" and surface in ("て", "で"):
                if a > 0 and a == n and prev_conj:
                    a = _retract(a, pmoras)
                    events.append({"kind": "te_retract", "from": was, "to": a})
            elif ctype == "助動詞-マス" or (not feat and surface in ("ます", "まし", "ませ", "ましょう")):
                if a == 0 or a_src == "verb":
                    v = m - 1 if str(feat.get("aModeType") or "").startswith("M1") or surface == "ましょう" else 1
                    a = n + v
                    a_src = "verb"
                    events.append({"kind": "masu_steal", "was_accented": was > 0})
            elif ctype.startswith("助動詞-デス") or (not feat and surface in ("です", "でし", "でしょう")):
                if a == 0:
                    v = m - 1 if str(feat.get("aModeType") or "").startswith("M1") or surface == "でしょう" else 1
                    a = n + v
                    events.append({"kind": "desu_accent"})
            elif ctype.startswith("助動詞-ダ") or (not feat and surface in ("だっ", "なら", "じゃ", "な", "で")):
                if surface in ("だっ", "なら", "だろう") and a == 0:
                    a = n + (m - 1 if surface == "だろう" else 1)
                    events.append({"kind": "desu_accent"})
            elif ctype.startswith("助動詞-タイ") or (not feat and surface in ("たい", "たく", "たかっ")):
                if a == 0 or a_src == "verb":
                    a = n + 1
                    a_src = "verb"
                    events.append({"kind": "tai_accent", "was_accented": was > 0})
            elif ctype.startswith("助動詞-ナイ") or (not feat and surface in ("ない", "なかっ", "なけれ")):
                # relocation applies only when the fall sits inside the verb
                # stem ない attaches to (はしら↓ない) — a fall on an earlier
                # element stays put (わか↓っていない)
                if prev is not None and prev.get("pos") == "動詞" and a_src == "verb" \
                        and a > n - len(prev.get("moras") or []):
                    a = n                # fall lands right before ない
                    if was != a:
                        events.append({"kind": "nai_relocate", "from": was, "to": a})
                if a == 0 and surface in ("なかっ", "なけれ"):
                    a = n + max(m - 2, 1)   # M2@2: かわな↓かった
                    a_src = "verb"
                    events.append({"kind": "nakatta_accent"})
            elif ctype.startswith("助動詞-ヌ") or (not feat and surface in ("ず", "ん") and pos == "助動詞"):
                if a == 0 or a_src == "verb":
                    a = n                # F4@0: いか↓ず, いきませ↓ん
                    a_src = "verb"
            elif pos == "助詞" and pos2 == "終助詞":
                pass                     # ね/よ/か/な: boundary intonation, not accent
            elif pos == "助詞" and surface in ("しか", "だけ", "ほど", "って"):
                pass                     # neutral particles (unidic F2 overshoots)
            elif pos == "助詞" and surface == "の":
                # の never creates a fall (unidic's 動詞%F2@0 clause overshoots:
                # 買うのが stays flat); after nominal odaka hosts it deaccents
                if headcat == "名詞" and a > 0 and (
                    a == n or (a == n - 1 and pmoras and pmoras[-1] in DEFECTIVE_MORAS)
                ):
                    a = 0                # やまの, にほんの
                    events.append({"kind": "no_deaccent"})
            elif pos == "助詞" and surface in SELF_ACCENTING_PARTICLES:
                if a == 0:
                    a = n + SELF_ACCENTING_PARTICLES[surface]
                    events.append({"kind": "particle_accent", "surface": surface})
            elif pos == "助詞" and headcat in ("動詞", "形容詞") and surface != "ば" \
                    and surface not in SELF_ACCENTING_PARTICLES:
                # case/topic particles never create a fall on a flat verb
                # phrase (買うのが, 置いてから, やったん… all stay flat) —
                # unidic's 動詞%F2@0 clauses overshoot for neutral diagrams
                pass
            else:
                rule = _acon_rule(feat.get("aConType"), headcat)
                if rule:
                    kind, v = rule
                    if str(feat.get("aModeType") or "").startswith("M1"):
                        v = m - 1
                    if kind == "F2" and a == 0:
                        a = n + v
                    elif kind == "F3" and a > 0 and a_src == "verb":
                        a = n + v        # られる/れる relocate their verb chain's fall
                    elif kind == "F4" and (a == 0 or (pos == "助動詞" and a_src == "verb")):
                        a = n + v
                    elif kind == "F5":
                        a = 0
                    if a != was and a > 0:
                        if pos == "助動詞":
                            a_src = "verb"
                        events.append({"kind": "rule_accent", "surface": surface})

        pmoras.extend(w["moras"])
        n += m
        prev = w
        prev_conj = is_verbal(w)

    return min(a, n), events


def group_accent_phrases(words: list[dict]) -> list[dict]:
    """Merge tokenized words into accent phrases with downstep levels.

    Returns one dict per phrase: moras, accent (combination rules applied),
    H/L pattern, downstep level (0 = first/reset, higher = lower plateau),
    word boundaries for labeling, break_after where punctuation resets the
    intonation, and the rule events that fired (for hint generation)."""
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
        feat = w.get("feat") or {}
        attach = False
        if cur is not None:
            last = cur[-1]
            lfeat = last.get("feat") or {}
            if w.get("pos") in ATTACH_POS:
                attach = True
            elif last.get("pos") == "接頭辞":
                attach = True
            elif last["surface"] in DEMONSTRATIVES and last.get("pos") in ("連体詞", "感動詞"):
                # unidic tags prenominal あの as 感動詞 filler — still merges
                attach = True
            elif w.get("pos") == "感動詞" and feat.get("pos2") == "フィラー" \
                    and last.get("pos") == "感動詞" and (lfeat.get("pos2") == "フィラー"):
                attach = True   # unidic splits まあ into ま+あ fillers
            elif w.get("pos") == "動詞":
                # 補助動詞 after て/で (〜ている), じゃ+ありません, or する
                # after its サ変 noun — these cliticize instead of opening a
                # new phrase
                if feat.get("pos2") == "非自立可能" and last.get("pos") == "助詞" \
                        and last["surface"] in ("て", "で") \
                        and lfeat.get("pos2") == "接続助詞":
                    attach = True   # instrumental で (バスで行く) must not match
                elif feat.get("orthBase") in ("ある", "有る") and last.get("pos") == "助動詞" \
                        and last["surface"] in ("じゃ", "で"):
                    attach = True
                elif (feat.get("orthBase") in ("する", "為る", "できる", "出来る")
                      or feat.get("lemma") in ("為る", "出来る")) \
                        and last.get("pos") in ("名詞", "接尾辞"):
                    attach = True   # サ変: 勉強+する, 想像+できる
                elif (last.get("pos") == "動詞"
                      and str(lfeat.get("cForm") or "").startswith("連用形-一般")) \
                        or (last.get("pos") == "名詞"
                            and feat.get("orthBase") in COMPOUND_V2) \
                        or (last.get("pos") == "形容詞"
                            and str(lfeat.get("cForm") or "").startswith("語幹")
                            and feat.get("orthBase") in COMPOUND_V2):
                    # compound verbs unidic splits: 跳び(名詞!)+出し, 厳し+過ぎ
                    attach = True
            elif w.get("pos") == "形容詞" and feat.get("orthBase") in ("ない", "無い"):
                # 補助形容詞 ない only after its hosts (高く+ない, 学生じゃ+ない)
                # — as a standalone predicate (時間が無い) it opens a phrase
                if last.get("pos") == "助動詞" \
                        or (last.get("pos") == "形容詞" and str(lfeat.get("cForm") or "").startswith("連用形")) \
                        or str(lfeat.get("cType") or "").startswith("助動詞-タイ"):
                    attach = True
            elif w.get("pos") == "形状詞" and feat.get("pos2") == "助動詞語幹":
                attach = True   # そう/よう/みたい: 元気+そう, 嬉し+そう
            elif w["surface"] in COUNTER_ACCENT \
                    and (last.get("feat") or {}).get("pos2") == "数詞":
                attach = True   # counters unidic tags 名詞: １９９８+年
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
            ws = {"surface": w["surface"], "at": len(moras)}
            if w.get("is_target"):
                ws["target"] = True
            word_starts.append(ws)
            moras.extend(w["moras"])
        accent, events = _phrase_accent(group)
        phrases.append({
            "surface": "".join(w["surface"] for w in group),
            "moras": moras,
            "accent": accent,
            "pattern": accent_to_pattern(len(moras), accent),
            "level": level,
            "words": word_starts,
            "break_after": brk,
            "events": events,
        })
        if brk:
            level = 0
        elif accent > 0:
            level = min(level + 1, MAX_DOWNSTEP_LEVEL)
    return phrases


LEADING_KANA_RE = re.compile(r"^[ぁ-ゖァ-ヶー]+")


def fuse_target_runs(words: list[dict], expression: str, reading: str,
                     accent: int | None) -> list[dict]:
    """Fuse consecutive tokens whose concatenated surface is exactly the
    deck's target word (unidic splits 日本語 → 日本+語); the curated reading
    and accent then apply to the whole run."""
    if not expression:
        return words
    out: list[dict] = []
    i = 0
    while i < len(words):
        fused = None
        if words[i].get("moras"):
            surf = ""
            for j in range(i, min(i + 5, len(words))):
                if not words[j].get("moras"):
                    break
                surf += words[j]["surface"]
                if surf == expression and j > i:
                    moras = split_moras(reading) if reading else \
                        [m for r in words[i:j + 1] for m in r["moras"]]
                    # a fused word is a complete verb/adjective only when its
                    # tail is one; everything else (言い+方, 相+変わら+ず) acts
                    # as a noun/adverb — never as a prefix or verb chain
                    fused_pos = words[j]["pos"] if words[j]["pos"] in ("動詞", "形容詞") \
                        else "名詞"
                    fused = {
                        "surface": expression,
                        "kana": reading or "".join(w["kana"] for w in words[i:j + 1]),
                        "moras": moras,
                        "accent": accent,
                        "pos": fused_pos,
                        "content": True,
                        "feat": {"fused": True},
                        "is_target": True,
                    }
                    if accent is not None and moras:
                        fused["pattern"] = accent_to_pattern(len(moras), accent)
                    i = j + 1
                    break
                if len(surf) >= len(expression):
                    break
        if fused:
            out.append(fused)
        else:
            out.append(words[i])
            i += 1
    return out


def mark_target(words: list[dict], expression: str, citation_accent: int | None,
                citation_moras: list[str]) -> int | None:
    """Apply the deck's curated accent to the target word inside the sentence.

    Exact-surface occurrences take the citation accent directly. When the
    target only appears conjugated (見る → 見ます), match by lemma and keep
    unidic's form-specific accent — unless it disagrees with the deck on
    whether the word is accented at all, in which case the deck's class wins
    (the fall is put on the chain edge so て/た/ない rules place it)."""
    idx = None
    for i, w in enumerate(words):
        if w.get("is_target") or (
            w["surface"] == expression and len(w.get("moras", [])) == len(citation_moras or [])
        ):
            if citation_accent is not None and w["surface"] == expression and w.get("moras"):
                w["accent"] = citation_accent
                w["pattern"] = accent_to_pattern(len(w["moras"]), citation_accent)
            w["is_target"] = True
            if idx is None:
                idx = i
    if idx is not None:
        return idx
    def norm(s: str | None) -> str:
        return (s or "").replace("ずる", "じる")  # unidic lemma 信ずる vs deck 信じる

    for i, w in enumerate(words):
        feat = w.get("feat") or {}
        if expression and w.get("moras") and norm(expression) in (
            norm(feat.get("orthBase")), norm(feat.get("lemma")),
        ):
            w["is_target"] = True
            # reconcile accentedness class with the deck — but never for forms
            # that carry their own accent regardless of class (volitional
            # やろ↓う of flat やる: aModeType M1/M2)
            if citation_accent is not None and not str(feat.get("aModeType") or "").startswith("M"):
                form_accent = w.get("accent") or 0
                if citation_accent == 0 and form_accent:
                    w["accent"] = 0
                    w["pattern"] = accent_to_pattern(len(w["moras"]), 0)
                elif citation_accent > 0 and not form_accent:
                    w["accent"] = len(w["moras"])
                    w["pattern"] = accent_to_pattern(len(w["moras"]), w["accent"])
            return i
    # last resorts: the target hides inside a fused compound / 連体詞 token
    # (大混乱, 主な), or appears as its potential form (解く → 解けます)
    for i, w in enumerate(words):
        if expression and w.get("moras") and w.get("content") and expression in w["surface"]:
            w["is_target"] = True
            return i
    stem = expression[:-1] if len(expression) > 1 else ""
    for i, w in enumerate(words):
        feat = w.get("feat") or {}
        if stem and w.get("pos") == "動詞" and w.get("moras") \
                and str(feat.get("orthBase") or "").startswith(stem):
            w["is_target"] = True
            return i
    return None


def _render_accent(moras: list[str], accent: int) -> str:
    """かえりま↓す — kana with the fall marked, for hint text."""
    out = list(moras)
    if 0 < accent <= len(out):
        out.insert(accent, "↓")
    return "".join(out)


def build_sentence_hints(phrases: list[dict], words: list[dict], target_idx: int | None,
                         expression: str, citation_accent: int | None) -> list[str]:
    """Mini-explanations for genuinely tricky spots: places where the sentence
    melody legitimately contradicts the citation pattern shown in the word
    diagram, plus rare phenomena (の-deaccenting, self-accenting particles,
    devoiced accent moras). Obvious/regular things get no hint."""
    hints: list[str] = []
    target_phrase = None
    t_at = None
    for p in phrases:
        for ws in p["words"]:
            if ws.get("target"):
                target_phrase, t_at = p, ws["at"]
                break
        if target_phrase:
            break

    if target_phrase is not None:
        p = target_phrase
        evs = {e["kind"] for e in p.get("events", [])}
        r = _render_accent(p["moras"], p["accent"])
        tw = words[target_idx] if target_idx is not None else None
        n_t = len(tw["moras"]) if tw else 0
        is_verb = bool(tw) and tw.get("pos") in ("動詞", "形容詞")
        t_feat = (tw or {}).get("feat") or {}
        rule_surfaces = {e.get("surface") for e in p.get("events", []) if e["kind"] == "rule_accent"}
        if tw and tw.get("pos") == "接尾辞":
            hints.append(f"As a suffix, {expression} rides its host word — the whole phrase carries one accent: {r}")
        elif tw and t_feat.get("pos2") == "数詞" and p["accent"] != (t_at or 0) + (citation_accent or 0):
            hints.append(f"Numbers change accent with their counter — the pair is one unit: {r}")
        elif tw and t_feat.get("fused") and tw["surface"] != expression:
            hints.append(f"{expression} sits inside the compound {tw['surface']}, which has its own single accent: {r}")
        elif str(t_feat.get("cForm") or "").startswith("意志推量") and is_verb:
            hints.append(f"The volitional 〜う always accents its next-to-last mora: {r}")
        elif rule_surfaces & {"れる", "られる", "れ", "られ", "せる", "させる", "せ", "させ"} and is_verb:
            hints.append(f"れる/られる forms carry their own accent near the end: {r}")
        elif "masu_steal" in evs and is_verb:
            if citation_accent:
                hints.append(f"ます always takes the fall itself (〜ま↓す) — {expression}'s own drop disappears: {r}")
            else:
                hints.append(f"{expression} is flat on its own, but ます always brings a fall: {r}")
        elif ("ta_retract" in evs or "te_retract" in evs) and is_verb:
            hints.append(f"て/た forms pull an accented verb's fall back one mora: {r}")
        elif "nai_relocate" in evs and is_verb:
            hints.append(f"ない drags the fall to sit right before itself: {r}")
        elif "nakatta_accent" in evs and is_verb:
            hints.append(f"なかった supplies its own fall on な: {r}")
        elif "tai_accent" in evs and is_verb:
            hints.append(f"たい takes the fall onto its た: {r}")
        elif "desu_accent" in evs and citation_accent == 0 and not is_verb:
            hints.append(f"{expression} stays flat — the fall you hear belongs to です (で↓す after flat words): {r}")
        elif "no_deaccent" in evs:
            hints.append(f"{expression} loses its fall before の — odaka words flatten there: {r}")
        elif "particle_accent" in evs:
            prt = next(e["surface"] for e in p["events"] if e["kind"] == "particle_accent")
            hints.append(f"{prt} carries its own fall after a flat word: {r}")
        elif "c1_replace" in evs:
            hints.append(f"The attached word's accent replaces the host's here: {r}")
        elif (tw and citation_accent and n_t and citation_accent >= n_t
              and t_at is not None and p["accent"] == t_at + n_t and len(p["moras"]) > p["accent"]):
            hints.append(f"{expression} is odaka — the drop only shows up on the attached particle: {r}")
        if not hints and tw and citation_accent is not None and t_at is not None and n_t:
            rel = p["accent"] - t_at
            if (citation_accent == 0 and 0 < rel <= n_t) or \
                    (citation_accent > 0 and (p["accent"] == 0 or rel != citation_accent)):
                if is_verb and tw["surface"] != expression:
                    hints.append(f"The conjugated form of {expression} shifts its fall: {r}")
                else:
                    hints.append(f"In connected speech this phrase carries one accent — {expression}'s citation pattern gives way to: {r}")
        if p["accent"] > 0:
            i = p["accent"] - 1
            mor = p["moras"]
            if mor[i][0] in DEVOICEABLE and i + 1 < len(mor) and mor[i + 1][0] in VOICELESS_ONSET:
                hints.append(f"The accented {mor[i]} is devoiced (whispered) — the drop is heard as {mor[i + 1]} coming in low.")

    for p in phrases:
        if len(hints) >= 2:
            break
        if p is target_phrase:
            continue
        for e in p.get("events", []):
            if e["kind"] == "no_deaccent":
                hints.append(f"{p['surface']}: the fall disappears before の (odaka + の flattens): {_render_accent(p['moras'], p['accent'])}")
            elif e["kind"] == "particle_accent":
                hints.append(f"{p['surface']}: {e['surface']} carries its own fall after a flat word: {_render_accent(p['moras'], p['accent'])}")
    return hints[:2]


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

    def prime_deck(self, notes: list) -> None:
        """Register every note's curated accent so sentence tokenization
        renders each word with the same accent its own card shows."""
        curated: dict[tuple[str, str], int] = {}
        for note in notes:
            reading = (getattr(note, "reading", "") or note.expression).split("・")[0].strip()
            moras = split_moras(reading)
            accent = None
            parsed = parse_pitch_field(getattr(note, "pitch_html", "") or "")
            if parsed:
                seg = next((s for s in parsed if moras and len(s[0]) == len(moras)), parsed[0])
                accent = seg[1]
            elif getattr(note, "pitch_position", None) is not None:
                accent = note.pitch_position
            if accent is not None:
                curated[(note.expression, kata_to_hira(reading))] = accent
        set_curated_accents(curated)

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
            overrides: dict[str, list[str]] = {}
            for base, rd in FURIGANA_PAIR_RE.findall(getattr(note, "sentence_furigana", "") or ""):
                # anki only spaces pairs where needed, so the captured base can
                # drag in preceding kana (は日本[にほん]) — the reading always
                # belongs to the trailing non-kana run
                base = LEADING_KANA_RE.sub("", base)
                if base and rd not in overrides.setdefault(base, []):
                    overrides[base].append(rd)
            if reading:
                rds = overrides.setdefault(note.expression, [])
                if reading in rds:
                    rds.remove(reading)
                rds.insert(0, reading)
            words = tokenize_sentence(note.sentence, overrides)
            words = fuse_target_runs(words, note.expression, reading, accent)
            words = fuse_dictionary_runs(words)
            # the curated accent for the target word beats the tokenizer's
            # dictionary lookup wherever the word appears in the sentence
            target_idx = mark_target(words, note.expression, accent, moras)
            phrases = group_accent_phrases(words)
            if phrases:
                hints = build_sentence_hints(phrases, words, target_idx, note.expression, accent)
                for p in phrases:
                    p.pop("events", None)
                data["sentence_phrases"] = phrases
                if hints:
                    data["sentence_hints"] = hints
            for w in words:
                w.pop("feat", None)   # unidic internals — not needed downstream
            data["sentence_words"] = words
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
