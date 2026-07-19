"""Optional Yomitan pitch-accent dictionaries.

Any Yomitan pitch dictionary zip (format 3 — term_meta_bank_*.json entries
with mode == "pitch", e.g. the NHK 2016 or 三省堂 pitch dicts) dropped into
server/data/pitch_dicts/ is loaded lazily and consulted before the vendored
Kanjium database. The directory is gitignored: these dictionaries are
copyrighted material the user supplies, not something we ship.

Lookup is keyed on (surface, reading-in-hiragana); entries without a usable
pitch position are skipped. Multiple dictionaries merge, first-loaded wins
the ordering of candidate accents.
"""
from __future__ import annotations

import json
import zipfile
from functools import lru_cache

from .config import PITCH_DICTS_DIR
from .kanjium import get_kanjium, kata_to_hira


class YomitanPitchDB:
    def __init__(self) -> None:
        self.by_key: dict[tuple[str, str], list[int]] = {}
        for zpath in sorted(PITCH_DICTS_DIR.glob("*.zip")):
            try:
                self._load_zip(zpath)
            except Exception:
                continue  # a malformed dictionary shouldn't take the app down

    def _load_zip(self, zpath) -> None:
        with zipfile.ZipFile(zpath) as z:
            for name in z.namelist():
                if not name.startswith("term_meta_bank"):
                    continue
                # several published pitch dicts carry a wrong CRC on their
                # last bank (Yomitan ignores CRCs); the JSON parse below is
                # our integrity check instead
                with z.open(name) as f:
                    f._expected_crc = None  # type: ignore[attr-defined]
                    raw = f.read()
                for entry in json.loads(raw):
                    if len(entry) != 3 or entry[1] != "pitch" or not isinstance(entry[2], dict):
                        continue
                    term, _, obj = entry
                    reading = kata_to_hira(obj.get("reading") or term)
                    accents: list[int] = []
                    for p in obj.get("pitches") or []:
                        pos = p.get("position")
                        if isinstance(pos, int) and pos >= 0 and pos not in accents:
                            accents.append(pos)
                    if not accents:
                        continue
                    existing = self.by_key.setdefault((term, reading), [])
                    for a in accents:
                        if a not in existing:
                            existing.append(a)

    def lookup(self, surface: str, reading: str | None = None) -> list[int] | None:
        if not reading:
            return None
        reading = kata_to_hira(reading)
        return self.by_key.get((surface, reading)) or self.by_key.get((reading, reading))


@lru_cache(maxsize=1)
def get_pitch_dicts() -> YomitanPitchDB:
    return YomitanPitchDB()


def lookup_accents(surface: str, reading: str | None = None) -> tuple[list[int], str] | None:
    """Best available accent numbers + which tier answered.

    User-supplied Yomitan pitch dictionaries win over Kanjium: they're
    curated (NHK etc.) where Kanjium is an aggregate.
    """
    hit = get_pitch_dicts().lookup(surface, reading)
    if hit:
        return hit, "dict"
    hit = get_kanjium().lookup(surface, reading)
    if hit:
        return hit, "kanjium"
    return None
