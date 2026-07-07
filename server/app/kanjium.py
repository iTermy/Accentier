"""Kanjium pitch-accent database loader.

Data: vendor/kanjium_accents.txt from https://github.com/mifunetoshiro/kanjium
(TSV: surface, reading, accent numbers). ~124k entries, same source Yomitan's
pitch dictionaries are built from. Loaded lazily into two indexes:
  (surface, reading_hiragana) -> [accent numbers]
  surface -> [(reading, accents), ...]   (fallback when reading unknown)
"""
from __future__ import annotations

import re
from functools import lru_cache

from .config import KANJIUM_PATH

_ACCENT_NUM_RE = re.compile(r"\d+")


def kata_to_hira(s: str) -> str:
    return "".join(chr(ord(c) - 0x60) if "ァ" <= c <= "ヶ" else c for c in s)


def hira_to_kata(s: str) -> str:
    return "".join(chr(ord(c) + 0x60) if "ぁ" <= c <= "ゖ" else c for c in s)


class KanjiumDB:
    def __init__(self) -> None:
        self.by_key: dict[tuple[str, str], list[int]] = {}
        self.by_surface: dict[str, list[tuple[str, list[int]]]] = {}
        self._load()

    def _load(self) -> None:
        if not KANJIUM_PATH.exists():
            return
        with open(KANJIUM_PATH, encoding="utf-8") as f:
            for line in f:
                parts = line.rstrip("\n").split("\t")
                if len(parts) != 3:
                    continue
                surface, reading, accents_raw = parts
                reading = kata_to_hira(reading) if reading else kata_to_hira(surface)
                accents: list[int] = []
                for chunk in accents_raw.split(","):
                    m = _ACCENT_NUM_RE.search(chunk)
                    if m:
                        a = int(m.group(0))
                        if a not in accents:
                            accents.append(a)
                if not accents:
                    continue
                self.by_key[(surface, reading)] = accents
                self.by_surface.setdefault(surface, []).append((reading, accents))

    def lookup(self, surface: str, reading: str | None = None) -> list[int] | None:
        """Return candidate accent numbers, best match first."""
        if reading:
            reading = kata_to_hira(reading)
            hit = self.by_key.get((surface, reading))
            if hit:
                return hit
            # try the reading itself as surface (kana words)
            hit = self.by_key.get((reading, reading))
            if hit:
                return hit
        entries = self.by_surface.get(surface)
        if entries:
            return entries[0][1]
        if reading:
            entries = self.by_surface.get(reading)
            if entries:
                return entries[0][1]
        return None


@lru_cache(maxsize=1)
def get_kanjium() -> KanjiumDB:
    return KanjiumDB()
