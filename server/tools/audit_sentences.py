"""Audit harness: build sentence accent-phrase diagrams for every Kaishi note
and dump them + suspicion flags for review.

Usage: python audit_sentences.py [--out DIR]
Writes:
  audit.jsonl   one record per note (expression, accent, phrases, flags)
  stats.txt     aggregate stats: flag counts, aux/particle frequency tables
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))

import app.languages.generic  # noqa: E402,F401  (registers the language modules)
import app.languages.japanese  # noqa: E402,F401
from app.apkg import parse_apkg  # noqa: E402
from app.config import KAISHI_APKG  # noqa: E402
from app.languages.base import get_module  # noqa: E402


def phrase_repr(p: dict) -> str:
    """Compact human-readable phrase: moras with ↓ at the fall, level, words."""
    moras = list(p["moras"])
    acc = p["accent"]
    if acc and 0 < acc <= len(moras):
        moras = moras[:acc] + ["↓"] + moras[acc:]
    return "".join(moras) + f"[{acc}]L{p['level']}" + ("‖" if p.get("break_after") else "")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(Path(__file__).parent))
    args = ap.parse_args()
    out_dir = Path(args.out)

    parsed, archive = parse_apkg(KAISHI_APKG, "Kaishi 1.5k")
    module = get_module("ja")
    if hasattr(module, "prime_deck"):
        module.prime_deck(parsed.notes)

    aux_counter: Counter[str] = Counter()   # 助動詞 surfaces
    prt_counter: Counter[str] = Counter()   # 助詞 surfaces
    suf_counter: Counter[str] = Counter()   # 接尾辞 surfaces
    flag_counter: Counter[str] = Counter()
    pos_unknown: Counter[str] = Counter()

    records = []
    try:
        for note in parsed.notes:
            if not note.sentence:
                continue
            acc = module.build_accent_data(note)
            words = acc.get("sentence_words") or []
            phrases = acc.get("sentence_phrases") or []
            flags: list[str] = []

            # target word located (exact surface, fused run, or lemma match)?
            if not any(w.get("is_target") for w in words):
                flags.append("TARGET_MISS")

            for w in words:
                pos = w.get("pos")
                if pos == "助動詞":
                    aux_counter[w["surface"]] += 1
                elif pos == "助詞":
                    prt_counter[w["surface"]] += 1
                elif pos == "接尾辞":
                    suf_counter[w["surface"]] += 1
                if (
                    w.get("content")
                    and w.get("moras")
                    and w.get("accent") is None
                ):
                    flags.append("UNKNOWN_ACCENT")
                    pos_unknown[f"{w['surface']}({pos})"] += 1

            hints = acc.get("sentence_hints") or []

            # does the target word's rendered fall match its citation accent?
            t_citation = acc.get("accent")
            diverge = None
            for p in phrases:
                tws = next((ws for ws in p["words"] if ws.get("target")), None)
                if tws is None:
                    continue
                tword = next((w for w in words if w.get("is_target")), None)
                n_t = len(tword["moras"]) if tword else 0
                rel = p["accent"] - tws["at"]
                if t_citation is None or not n_t:
                    break
                if t_citation == 0 and 0 < rel <= n_t:
                    diverge = f"flat word shows fall at {rel}"
                elif t_citation > 0 and (p["accent"] == 0 or rel <= 0 or rel > n_t):
                    diverge = f"accent [{t_citation}] vanished (phrase fall {p['accent']} at rel {rel})"
                elif t_citation > 0 and rel != t_citation:
                    diverge = f"accent [{t_citation}] rendered at rel {rel}"
                break
            if diverge and not hints:
                flags.append("TARGET_DIVERGE_UNEXPLAINED")

            for p in phrases:
                if p["accent"] > len(p["moras"]):
                    flags.append("ACCENT_OVERFLOW")
                if len(p["moras"]) > 14:
                    flags.append("LONG_PHRASE")

            for f in set(flags):
                flag_counter[f] += 1

            records.append({
                "note_id": note.note_id,
                "expression": note.expression,
                "reading": note.reading,
                "word_accent": acc.get("accent"),
                "sentence": note.sentence,
                "furigana": note.sentence_furigana,
                "hints": hints,
                "diverge": diverge,
                "phrases": [phrase_repr(p) for p in phrases],
                "raw_phrases": [
                    {k: p[k] for k in ("surface", "moras", "accent", "level", "break_after")}
                    for p in phrases
                ],
                "words": [
                    {k: w.get(k) for k in ("surface", "kana", "accent", "pos")}
                    for w in words
                ],
                "flags": sorted(set(flags)),
            })
    finally:
        archive.cleanup()

    with open(out_dir / "audit.jsonl", "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    with open(out_dir / "stats.txt", "w", encoding="utf-8") as f:
        f.write(f"notes with sentences: {len(records)}\n\nFLAGS:\n")
        for k, v in flag_counter.most_common():
            f.write(f"  {k}: {v}\n")
        f.write("\n助動詞 surfaces:\n")
        for k, v in aux_counter.most_common():
            f.write(f"  {k}: {v}\n")
        f.write("\n助詞 surfaces:\n")
        for k, v in prt_counter.most_common(60):
            f.write(f"  {k}: {v}\n")
        f.write("\n接尾辞 surfaces:\n")
        for k, v in suf_counter.most_common(40):
            f.write(f"  {k}: {v}\n")
        f.write("\nunknown-accent content words:\n")
        for k, v in pos_unknown.most_common(80):
            f.write(f"  {k}: {v}\n")
    print(f"wrote {len(records)} records")


if __name__ == "__main__":
    main()
