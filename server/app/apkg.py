"""Anki .apkg parser.

Supports both package generations:
- Legacy (schema 11): plain `collection.anki2` SQLite + JSON `media` map,
  media files stored uncompressed under numeric names.
- Modern (Anki 2.1.50+, `meta` version 2/3): zstd-compressed
  `collection.anki21b` SQLite + zstd protobuf `media` map, media files
  individually zstd-compressed.

Note models are read from either the `notetypes`/`fields` tables (new schema)
or the `col.models` JSON blob (legacy schema).

Field mapping: we auto-detect common mining-deck field names (Lapis, JPMN,
Kaishi, generic Yomitan exports) and fall back to heuristics — any field whose
value contains [sound:...] is an audio candidate; the first non-empty short
field is the expression.
"""
from __future__ import annotations

import json
import re
import sqlite3
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import zstandard

SOUND_RE = re.compile(r"\[sound:([^\]]+)\]")
TAG_RE = re.compile(r"<[^>]+>")
FURIGANA_RE = re.compile(r" ?([^ >\[\]]+)\[([^\]]+)\]")  # anki furigana syntax 漢字[かんじ]

# Candidate field names, in priority order. Note-type field names are
# normalized (lowercased, separators stripped) before matching, so
# "Sentence Audio", "sentence-audio" and "SentenceAudio" all match
# "sentenceaudio". Covers Lapis, JPMN, Kaishi, Core2k-style and Yomitan
# exports plus Japanese-named fields.
FIELD_CANDIDATES: dict[str, list[str]] = {
    "expression": ["expression", "word", "target", "targetword", "vocab", "vocabword",
                   "vocabulary", "term", "front", "key", "単語", "語彙", "表現"],
    "reading": ["expressionreading", "reading", "wordreading", "vocabreading",
                "vocabularyreading", "termreading", "vocabkana", "kana", "yomi",
                "読み", "よみ", "読み方"],
    "expression_furigana": ["expressionfurigana", "furigana", "vocabfurigana",
                            "wordfurigana", "termfurigana"],
    "sentence": ["sentence", "example", "examplesentence", "expression2", "context",
                 "sentencekanji", "例文", "文"],
    "sentence_furigana": ["sentencefurigana", "sentencereading", "sentencewithfurigana"],
    "sentence_audio": ["sentenceaudio", "sentencesound", "audiosentence", "sentaudio",
                       "exampleaudio", "contextaudio", "audioonfront", "例文音声", "文音声"],
    "word_audio": ["expressionaudio", "wordaudio", "vocabaudio", "vocabularyaudio",
                   "termaudio", "audioword", "wordsound", "expressionsound", "audio",
                   "sound", "単語音声", "音声"],
    "pitch_position": ["pitchposition", "pitchnumber", "accentposition", "pitchaccent",
                       "pitch", "accent", "ピッチ", "アクセント"],
    "pitch_categories": ["pitchcategories", "pitchcategory", "accentcategory", "pitchtype"],
}

# separators people put in field names; stripped before matching
_FIELD_NORM_RE = re.compile(r"[\s_\-:：·.·/\\()（）\[\]'\"]+")


def _normalize_field_name(name: str) -> str:
    return _FIELD_NORM_RE.sub("", name.lower())


@dataclass
class ParsedNote:
    note_id: int
    expression: str = ""
    reading: str = ""
    sentence: str = ""
    sentence_audio: str = ""   # media filename as referenced in the note
    word_audio: str = ""
    pitch_position: int | None = None
    pitch_categories: str = ""


@dataclass
class ParsedDeck:
    name: str
    notes: list[ParsedNote] = field(default_factory=list)
    media_used: dict[str, str] = field(default_factory=dict)  # real filename -> zip member name


def strip_html(s: str) -> str:
    s = SOUND_RE.sub("", s)
    s = s.replace("<br>", " ").replace("<br/>", " ").replace("<br />", " ")
    s = TAG_RE.sub("", s)
    s = s.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
    return s.strip()


def strip_furigana(s: str) -> str:
    """Turn 漢字[かんじ] anki furigana markup into plain 漢字."""
    return FURIGANA_RE.sub(r"\1", s)


def first_sound(s: str) -> str:
    m = SOUND_RE.search(s)
    return m.group(1).strip() if m else ""


def _parse_pitch_position(raw: str) -> int | None:
    """Yomitan exports positions as HTML like <span>[</span><span>1</span>... or plain '1' / '[1]'."""
    text = strip_html(raw)
    m = re.search(r"\d+", text)
    return int(m.group(0)) if m else None


def _decompress_zstd(data: bytes) -> bytes:
    return zstandard.ZstdDecompressor().decompress(data, max_output_size=1 << 31)


def _parse_media_map_protobuf(data: bytes) -> list[str]:
    """Minimal protobuf reader for the modern media map.

    Message: repeated MediaEntry entries = 1;
    MediaEntry { string name = 1; uint32 size = 2; bytes sha1 = 3; }
    Zip member name is the entry's index as a string.
    """
    names: list[str] = []
    i, n = 0, len(data)

    def read_varint(pos: int) -> tuple[int, int]:
        result = shift = 0
        while True:
            b = data[pos]
            result |= (b & 0x7F) << shift
            pos += 1
            if not b & 0x80:
                return result, pos
            shift += 7

    while i < n:
        tag, i = read_varint(i)
        wire = tag & 7
        if wire == 2:
            length, i = read_varint(i)
            payload = data[i:i + length]
            i += length
            if tag >> 3 == 1:  # MediaEntry
                j = 0
                name = ""
                while j < len(payload):
                    ftag, j = read_varint_from(payload, j)
                    fwire = ftag & 7
                    if fwire == 2:
                        flen, j = read_varint_from(payload, j)
                        if ftag >> 3 == 1:
                            name = payload[j:j + flen].decode("utf-8", "replace")
                        j += flen
                    elif fwire == 0:
                        _, j = read_varint_from(payload, j)
                    else:
                        break
                names.append(name)
        elif wire == 0:
            _, i = read_varint(i)
        else:
            break
    return names


def read_varint_from(data: bytes, pos: int) -> tuple[int, int]:
    result = shift = 0
    while True:
        b = data[pos]
        result |= (b & 0x7F) << shift
        pos += 1
        if not b & 0x80:
            return result, pos
        shift += 7


class ApkgArchive:
    """Wraps an open .apkg zip; knows how to read the collection DB and media."""

    def __init__(self, path: str | Path):
        self.zip = zipfile.ZipFile(path)
        names = set(self.zip.namelist())
        self.modern = "collection.anki21b" in names
        self._media_map: dict[str, str] = {}  # real filename -> zip member
        self._load_media_map(names)
        self._tmp_db: str | None = None

    def _load_media_map(self, names: set[str]) -> None:
        if "media" not in names:
            return
        raw = self.zip.read("media")
        if self.modern:
            entries = _parse_media_map_protobuf(_decompress_zstd(raw))
            self._media_map = {name: str(idx) for idx, name in enumerate(entries)}
        else:
            mapping = json.loads(raw.decode("utf-8"))  # zip member -> real name
            self._media_map = {v: k for k, v in mapping.items()}

    @property
    def media_filenames(self) -> set[str]:
        return set(self._media_map)

    def read_media(self, real_name: str) -> bytes | None:
        member = self._media_map.get(real_name)
        if member is None:
            return None
        data = self.zip.read(member)
        if self.modern:
            data = _decompress_zstd(data)
        return data

    def open_collection(self) -> sqlite3.Connection:
        if self.modern:
            db_bytes = _decompress_zstd(self.zip.read("collection.anki21b"))
        else:
            member = "collection.anki21" if "collection.anki21" in self.zip.namelist() else "collection.anki2"
            db_bytes = self.zip.read(member)
        tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
        tmp.write(db_bytes)
        tmp.close()
        self._tmp_db = tmp.name
        conn = sqlite3.connect(tmp.name)
        conn.row_factory = sqlite3.Row
        return conn

    def cleanup(self) -> None:
        self.zip.close()
        if self._tmp_db:
            Path(self._tmp_db).unlink(missing_ok=True)


def _load_notetype_fields(conn: sqlite3.Connection) -> dict[int, list[str]]:
    """Return {notetype_id: [field names in order]} for both schema generations."""
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    result: dict[int, list[str]] = {}
    if "fields" in tables and "notetypes" in tables:
        for row in conn.execute("SELECT ntid, ord, name FROM fields ORDER BY ntid, ord"):
            result.setdefault(row["ntid"], []).append(row["name"])
    if not result:
        models_json = conn.execute("SELECT models FROM col").fetchone()[0]
        models = json.loads(models_json)
        for mid, model in models.items():
            flds = sorted(model["flds"], key=lambda f: f["ord"])
            result[int(mid)] = [f["name"] for f in flds]
    return result


def _build_field_index(field_names: list[str]) -> dict[str, int]:
    """Map our canonical roles to field ordinals for one notetype."""
    norm = [_normalize_field_name(fn) for fn in field_names]
    mapping: dict[str, int] = {}
    for role, candidates in FIELD_CANDIDATES.items():
        for cand in candidates:
            if cand in norm:
                idx = norm.index(cand)
                if idx not in mapping.values():
                    mapping[role] = idx
                    break
    return mapping


def parse_apkg(path: str | Path, deck_name: str | None = None) -> tuple[ParsedDeck, ApkgArchive]:
    """Parse notes out of an .apkg. Caller must call archive.cleanup() when done
    (after extracting whatever media it wants)."""
    archive = ApkgArchive(path)
    conn = archive.open_collection()
    try:
        nt_fields = _load_notetype_fields(conn)
        nt_mapping = {ntid: _build_field_index(names) for ntid, names in nt_fields.items()}

        deck = ParsedDeck(name=deck_name or Path(path).stem)
        for row in conn.execute("SELECT id, mid, flds FROM notes"):
            fields_list = row["flds"].split("\x1f")
            mapping = nt_mapping.get(row["mid"], {})

            def get(role: str) -> str:
                idx = mapping.get(role)
                return fields_list[idx] if idx is not None and idx < len(fields_list) else ""

            expression = strip_html(strip_furigana(get("expression")))
            sentence = get("sentence") or get("sentence_furigana")
            sentence_plain = strip_html(strip_furigana(sentence))
            reading = strip_html(get("reading"))
            if not reading:
                furi = get("expression_furigana")
                if furi:
                    # extract just the kana from 漢字[かんじ] markup
                    kana_parts = FURIGANA_RE.findall(strip_html(furi))
                    reading = "".join(k for _, k in kana_parts) if kana_parts else ""

            sentence_audio = first_sound(get("sentence_audio"))
            word_audio = first_sound(get("word_audio"))
            if not sentence_audio and not word_audio:
                # heuristic: scan all fields for [sound:...]
                sounds = [s for f in fields_list for s in SOUND_RE.findall(f)]
                if sounds:
                    word_audio = sounds[0].strip()
                    if len(sounds) > 1:
                        sentence_audio = sounds[-1].strip()

            if not expression and not sentence_plain:
                continue
            # skip template/setup notes some shared decks ship with
            if "placeholder" in expression.lower() or "placeholder" in sentence_plain.lower():
                continue

            note = ParsedNote(
                note_id=row["id"],
                expression=expression or sentence_plain[:40],
                reading=reading,
                sentence=sentence_plain,
                sentence_audio=sentence_audio,
                word_audio=word_audio,
                pitch_position=_parse_pitch_position(get("pitch_position")),
                pitch_categories=strip_html(get("pitch_categories")),
            )
            deck.notes.append(note)

        available = archive.media_filenames
        for note in deck.notes:
            for fname in (note.sentence_audio, note.word_audio):
                if fname and fname in available:
                    deck.media_used[fname] = fname
        return deck, archive
    finally:
        conn.close()
