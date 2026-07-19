"""Parser for the Kaishi 1.5k deck's curated "Pitch Accent" field.

The field renders pitch the Migaku way — kana with an overline over high
moras — in one of three HTML variants that coexist in the deck:

  1. wrapper spans:   high moras sit inside
     <span style="display:inline-block;position:relative;...">, whose second
     child span draws the overline; a downstep after the group is drawn with
     border-right on that bar (and usually padding-right on the wrapper),
  2. plain overline:  <span style="text-decoration:overline;">ンキョー</span>,
  3. bare text:       no markup at all → heiban.

Alternate patterns/readings are separated by ・ and sometimes flagged with a
trailing *. Nasalized ガ行 is rendered as カ行 + a red ° (skipped when
counting moras); a couple of entries use CJK lookalike characters (匕 for ヒ,
二 for ニ) which are normalized.

Validated against the deck: all 1500 notes parse, and of the 1477 words the
pitch dictionaries also cover, 1466 agree (the remaining 11 are deliberate
curation differences, e.g. ところ marked odaka where NHK lists heiban —
the deck value wins, dictionary values are kept as alternates).
"""
from __future__ import annotations

import re
from html.parser import HTMLParser

from .kanjium import hira_to_kata

KATA_RE = re.compile(r"[ァ-ヶー]")
SMALL_KANA = set("ャュョァィゥェォヮ")
# CJK lookalikes that appear in the deck's pitch fields instead of katakana
CONFUSABLES = {"匕": "ヒ", "二": "ニ", "力": "カ", "工": "エ", "口": "ロ", "卜": "ト", "夕": "タ"}


def _norm_kana(ch: str) -> str:
    return hira_to_kata(CONFUSABLES.get(ch, ch))


class _PitchHTML(HTMLParser):
    """Flatten pitch HTML into (kana_char, high, drop_after_this_char)."""

    def __init__(self) -> None:
        super().__init__()
        self.events: list[tuple[str, bool, bool]] = []
        self.stack: list[dict] = []

    def handle_starttag(self, tag, attrs):
        if tag != "span":
            return
        style = (dict(attrs).get("style") or "").replace(" ", "")
        frame = {
            "high": "position:relative" in style or "text-decoration:overline" in style,
            "drop": "padding-right" in style,
            "bar": "position:absolute" in style,  # the drawn overline bar itself
        }
        if frame["bar"] and "border-right-style:solid" in style and self.stack:
            # the downstep tick is drawn on the bar; it belongs to the wrapper
            self.stack[-1]["drop"] = True
        self.stack.append(frame)

    def handle_endtag(self, tag):
        if tag == "span" and self.stack:
            frame = self.stack.pop()
            if frame["drop"]:
                # mark the last *kana* emitted inside this wrapper (skip °/*)
                for i in range(len(self.events) - 1, -1, -1):
                    ch, high, _ = self.events[i]
                    if KATA_RE.match(_norm_kana(ch)):
                        self.events[i] = (ch, high, True)
                        break

    def handle_data(self, data):
        if any(f["bar"] for f in self.stack):
            return
        high = any(f["high"] for f in self.stack)
        for ch in data:
            self.events.append((ch, high, False))


def parse_pitch_field(html: str) -> list[tuple[list[str], int]]:
    """Parse one Pitch Accent field into [(moras, accent), ...] — one entry
    per alternate pattern, in the deck's order (first = preferred)."""
    if not html or not html.strip():
        return []
    p = _PitchHTML()
    p.feed(html)

    segments: list[list[tuple[str, bool, bool]]] = [[]]
    for ch, high, drop in p.events:
        if ch == "・":
            segments.append([])
            continue
        k = _norm_kana(ch)
        if KATA_RE.match(k):
            segments[-1].append((k, high, drop))

    out: list[tuple[list[str], int]] = []
    for seg in segments:
        moras: list[str] = []
        drops: list[bool] = []
        for k, _high, drop in seg:
            if k in SMALL_KANA and moras:
                moras[-1] += k
                drops[-1] = drops[-1] or drop
            else:
                moras.append(k)
                drops.append(drop)
        if not moras:
            continue
        if any(drops):
            accent = max(i for i, d in enumerate(drops) if d) + 1
        else:
            accent = 0  # overline with no downstep, or bare text → heiban
        out.append((moras, accent))
    return out
