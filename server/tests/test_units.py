"""Unit tests for the pieces with subtle behavior: DSP repair/smoothing,
approximate word alignment, SRS scheduling rules, and field-name matching.
Synthetic signals only — fast, no decks needed. Run:
    ../.venv/Scripts/python tests/test_units.py
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ["ACCENTIER_TEST"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app.config as config
config.DATA_DIR = Path(tempfile.mkdtemp(prefix="accentier_unit_"))
config.DB_PATH = config.DATA_DIR / "test.sqlite3"

import numpy as np


def test_yin_and_repair():
    from app.dsp.yin import yin_f0, _postprocess

    sr = 16000
    t = np.arange(sr * 2) / sr
    f = 180 + 8 * np.sin(2 * np.pi * 3 * t)
    x = np.sin(2 * np.pi * np.cumsum(f) / sr) * 0.5
    res = yin_f0(x, sr)
    voiced = res["f0"][~np.isnan(res["f0"])]
    assert len(voiced) > 150
    assert abs(voiced.mean() - 180) < 3, voiced.mean()

    # a 200 ms halving error inside a clean run gets shifted back up
    f0 = np.full(200, np.nan)
    f0[20:180] = 200 + 10 * np.sin(np.linspace(0, 6, 160))
    f0[80:100] /= 2
    fixed = _postprocess(f0.copy())
    assert np.nanmean(fixed[80:100]) > 150


def test_smoothing_bridges_short_gaps_only():
    from app.dsp.yin import smooth_semitones

    times = np.arange(200) * 0.01
    st = np.full(200, np.nan)
    st[10:60] = 1.0
    st[65:120] = -1.0   # 50 ms gap: consonant inside a word -> bridge
    st[160:190] = 0.5   # 400 ms gap: real pause -> keep
    sm = smooth_semitones(times, st)
    assert not np.isnan(sm[62])
    assert np.isnan(sm[140])


def test_smoothing_kills_vibrato_keeps_drops():
    from app.dsp.yin import smooth_semitones

    times = np.arange(300) * 0.01
    st = np.full(300, np.nan)
    # flat pitch with 6 Hz ±0.8 st vibrato, then a 3 st accent drop
    seg = np.arange(20, 260)
    base = np.where(seg < 140, 0.0, -3.0)
    st[seg] = base + 0.8 * np.sin(2 * np.pi * 6 * times[seg])
    sm = smooth_semitones(times, st)
    assert np.nanstd(sm[40:120]) < 0.3, "vibrato should be flattened out"
    assert abs(np.nanmean(sm[40:120])) < 0.4
    assert abs(np.nanmean(sm[170:250]) - (-3.0)) < 0.4, "drop level must survive"


def test_alignment_spans():
    from app.dsp.yin import yin_f0
    from app.alignment import speech_chunks, align_words

    sr = 16000
    tone = np.sin(2 * np.pi * 150 * np.arange(sr // 2) / sr) * 0.5
    x = np.concatenate([tone, np.zeros(sr // 2), tone])
    res = yin_f0(x, sr)
    chunks = speech_chunks(res["times"], res["rms"])
    assert len(chunks) == 2, chunks

    words = [{"surface": "AB", "moras": ["A", "B"], "accent": 1},
             {"surface": "CDE", "moras": ["C", "D", "E"], "accent": None}]
    spans = align_words(res["times"], res["rms"], words)
    assert spans and spans[0]["end"] <= spans[1]["start"] + 1e-9
    # spans must be JSON-serializable plain floats
    import json
    json.dumps(spans)


def test_edge_spur_removal():
    from app.dsp.yin import _remove_edge_spurs

    f0 = np.full(300, np.nan)
    f0[5:12] = 300.0     # 70 ms click blip at the start
    f0[60:200] = 150.0   # main utterance
    f0[240:246] = 140.0  # trailing blip (lip smack / mouse click)
    rms = np.full(300, 0.001)
    rms[5:12] = 0.05
    rms[60:200] = 0.2
    rms[240:246] = 0.05
    out = _remove_edge_spurs(f0, rms, hop_s=0.01)
    assert np.all(np.isnan(out[5:12])), "leading spur should be removed"
    assert np.all(np.isnan(out[240:246])), "trailing spur should be removed"
    assert not np.any(np.isnan(out[60:200])), "main utterance must be untouched"

    # a short leading run right next to speech (< min gap) is speech, keep it
    f0b = np.full(300, np.nan)
    f0b[50:58] = 150.0
    f0b[70:200] = 150.0
    rmsb = np.full(300, 0.2)
    outb = _remove_edge_spurs(f0b, rmsb, hop_s=0.01)
    assert not np.any(np.isnan(outb[50:58]))


def test_alignment_pause_anchoring():
    from app.dsp.yin import yin_f0
    from app.alignment import speech_chunks, align_words

    sr = 16000
    tone = np.sin(2 * np.pi * 150 * np.arange(sr // 2) / sr) * 0.5
    x = np.concatenate([tone, np.zeros(sr // 2), tone])
    res = yin_f0(x, sr)
    chunks = speech_chunks(res["times"], res["rms"])
    assert len(chunks) == 2, chunks

    # punctuation between the words -> each word anchored to its own chunk
    words = [{"surface": "AB", "moras": ["A", "B"], "accent": 1},
             {"surface": "、", "moras": [], "accent": None},
             {"surface": "CDE", "moras": ["C", "D", "E"], "accent": None}]
    spans = align_words(res["times"], res["rms"], words)
    assert spans and len(spans) == 2
    assert spans[0]["end"] <= chunks[0][1] + 1e-9, (spans, chunks)
    assert spans[1]["start"] >= chunks[1][0] - 1e-9, (spans, chunks)


def test_alignment_pause_without_punctuation():
    from app.dsp.yin import yin_f0
    from app.alignment import speech_chunks, align_words

    sr = 16000
    tone = np.sin(2 * np.pi * 150 * np.arange(sr // 2) / sr) * 0.5
    x = np.concatenate([tone, np.zeros(sr // 2), tone])
    res = yin_f0(x, sr)
    chunks = speech_chunks(res["times"], res["rms"])
    assert len(chunks) == 2

    # no punctuation between the words — the pause must still split them
    words = [{"surface": "AB", "moras": ["A", "B"], "accent": 1},
             {"surface": "CDE", "moras": ["C", "D", "E"], "accent": None}]
    spans = align_words(res["times"], res["rms"], words, res["f0"])
    assert spans and len(spans) == 2
    assert spans[0]["end"] <= chunks[0][1] + 1e-9, (spans, chunks)
    assert spans[1]["start"] >= chunks[1][0] - 1e-9, (spans, chunks)


def test_alignment_ignores_pitchless_noise():
    from app.dsp.yin import yin_f0
    from app.alignment import speech_chunks

    sr = 16000
    rng = np.random.default_rng(7)
    noise = rng.standard_normal(int(sr * 0.4)) * 0.25
    tone = np.sin(2 * np.pi * 150 * np.arange(sr) / sr) * 0.5
    x = np.concatenate([noise, np.zeros(int(sr * 0.3)), tone])
    res = yin_f0(x, sr)
    with_f0 = speech_chunks(res["times"], res["rms"], res["f0"])
    assert len(with_f0) == 1, with_f0
    assert with_f0[0][0] > 0.5, "noise must not pull the speech start earlier"


def test_devoicing_weights():
    from app.alignment import _mora_weights

    # utterance-final す of です devoices
    assert _mora_weights(["デ", "ス"]) == [1.0, 0.6]
    # ク before voiceless タ devoices; before voiced ダ it doesn't
    assert _mora_weights(["ク", "タ"])[0] == 0.6
    assert _mora_weights(["ク", "ダ"])[0] == 1.0


def test_yomitan_pitch_parsing():
    import json
    import zipfile
    from app.pitchdict import YomitanPitchDB

    zpath = config.DATA_DIR / "dict.zip"
    with zipfile.ZipFile(zpath, "w") as z:
        z.writestr("index.json", json.dumps({"title": "t", "format": 3}))
        z.writestr("term_meta_bank_1.json", json.dumps([
            ["雨", "pitch", {"reading": "あめ", "pitches": [{"position": 1}]}],
            ["飴", "pitch", {"reading": "アメ", "pitches": [{"position": 0}]}],
            ["頻度", "freq", 3],
        ]))
    db = object.__new__(YomitanPitchDB)
    db.by_key = {}
    db._load_zip(zpath)
    assert db.lookup("雨", "あめ") == [1]
    assert db.lookup("飴", "アメ") == [0], "katakana reading must normalize to hiragana"
    assert db.lookup("雨") is None, "no reading -> no guess"


def test_srs_rules():
    from app.db import init_db, get_conn
    from app import srs

    init_db()
    # db.py binds DB_PATH at first import, so when the whole suite runs in one
    # process this shares test_e2e's database — use ids that can't collide
    # with rows the e2e flow created.
    UID, DID, IID = 990, 990, 9900
    c = get_conn()
    c.execute("INSERT OR IGNORE INTO users VALUES (?,'srs_u','x',0)", (UID,))
    c.execute("INSERT OR IGNORE INTO decks (id,user_id,name,language,created_at) VALUES (?,?,'d','ja',0)", (DID, UID))
    c.execute("INSERT OR IGNORE INTO items (id,deck_id,expression,created_at) VALUES (?,?,'e',0)", (IID, DID))
    c.commit()

    r1 = srs.record_result(UID, IID, "sentence", 90)
    assert r1["outcome"] == "scheduled" and r1["reps"] == 1
    # immediate re-practice: schedule must not balloon
    r2 = srs.record_result(UID, IID, "sentence", 95)
    r3 = srs.record_result(UID, IID, "sentence", 95)
    assert r2["outcome"] == "early" and r3["outcome"] == "early"
    assert r3["interval_days"] == r1["interval_days"]
    # word mode is a separate schedule
    w = srs.record_result(UID, IID, "word", 90)
    assert w["outcome"] == "scheduled" and w["reps"] == 1
    # a bad take lapses even between reviews
    r4 = srs.record_result(UID, IID, "sentence", 20)
    assert r4["outcome"] == "lapse" and r4["reps"] == 0 and r4["lapses"] == 1


def test_field_matching():
    from app.apkg import _build_field_index

    idx = _build_field_index(["Sentence Audio", "Word-Audio", "Expression", "読み", "Pitch Accent"])
    assert idx["sentence_audio"] == 0
    assert idx["word_audio"] == 1
    assert idx["expression"] == 2
    assert idx["reading"] == 3
    assert idx["pitch_position"] == 4


def test_kaishi_pitch_field_parsing():
    from app.kaishi_pitch import parse_pitch_field

    BAR = ('<span style="border-color:currentColor;display:block;position:absolute;'
           'top:0.1em;left:0;right:0;height:0;border-top-width:0.1em;border-top-style:solid;{extra}"></span>')
    DROP_BAR = BAR.format(extra="right:-0.1em;height:0.4em;border-right-width:0.1em;border-right-style:solid;")

    def wrap(kana, drop=False, pad=False):
        style = "display:inline-block;position:relative;" + ("padding-right:0.1em;margin-right:0.1em;" if pad else "")
        return (f'<span style="{style}"><span style="display:inline;">{kana}</span>'
                + (DROP_BAR if drop else BAR.format(extra="")) + "</span>")

    # heiban: overline continues to the end, no drop tick
    assert parse_pitch_field("ワ" + wrap("タシ")) == [(["ワ", "タ", "シ"], 0)]
    # nakadaka with wrapper padding marking the drop
    assert parse_pitch_field("ア" + wrap("ナ", drop=True, pad=True) + "タ") == [(["ア", "ナ", "タ"], 2)]
    # drop drawn on the bar only (no wrapper padding)
    assert parse_pitch_field("イ" + wrap("チ", drop=True)) == [(["イ", "チ"], 2)]
    # plain-overline variant + long vowel mora
    assert parse_pitch_field('ベ<span style="text-decoration:overline;">ンキョー</span>') == \
        [(["ベ", "ン", "キョ", "ー"], 0)]
    # bare text = heiban
    assert parse_pitch_field("コ") == [(["コ"], 0)]
    # nasalized ° marker must not swallow the drop or add a mora
    assert parse_pitch_field(
        "ウ" + wrap('コ<span style="color: red;">°</span>', drop=True, pad=True) + "ク"
    ) == [(["ウ", "コ", "ク"], 2)]
    # ・-separated alternates, first pattern preferred
    alts = parse_pitch_field("ヒ" + wrap("ト") + "・ヒ" + wrap("ト", drop=True, pad=True))
    assert alts == [(["ヒ", "ト"], 0), (["ヒ", "ト"], 2)]
    # CJK lookalike 二 (U+4E8C) normalizes to katakana ニ
    assert parse_pitch_field(wrap("二", drop=True, pad=True)) == [(["ニ"], 1)]


def test_accent_phrase_grouping():
    from app.languages.japanese import group_accent_phrases

    words = [
        {"surface": "水", "moras": ["ミ", "ズ"], "accent": 0, "pos": "名詞"},
        {"surface": "は", "moras": ["ワ"], "accent": None, "pos": "助詞"},
        {"surface": "飲み", "moras": ["ノ", "ミ"], "accent": 1, "pos": "動詞"},
        {"surface": "ます", "moras": ["マ", "ス"], "accent": None, "pos": "助動詞"},
        {"surface": "。", "moras": [], "accent": None, "pos": "補助記号"},
    ]
    ph = group_accent_phrases(words)
    assert len(ph) == 2
    assert ph[0]["surface"] == "水は" and ph[0]["accent"] == 0, ph[0]
    assert ph[0]["pattern"] == [0, 1, 1]
    assert ph[1]["accent"] == 3, "ます takes the accent: のみま↓す"
    assert ph[1]["break_after"] is True

    # downstep: an accented phrase lowers the next one's plateau
    words2 = [
        {"surface": "猫", "moras": ["ネ", "コ"], "accent": 1, "pos": "名詞"},
        {"surface": "が", "moras": ["ガ"], "accent": None, "pos": "助詞"},
        {"surface": "来る", "moras": ["ク", "ル"], "accent": 1, "pos": "動詞"},
    ]
    ph2 = group_accent_phrases(words2)
    assert ph2[0]["surface"] == "猫が" and ph2[0]["accent"] == 1
    assert ph2[0]["level"] == 0 and ph2[1]["level"] == 1

    # です after an all-heiban phrase becomes accented (みずで↓す)
    words3 = [
        {"surface": "水", "moras": ["ミ", "ズ"], "accent": 0, "pos": "名詞"},
        {"surface": "です", "moras": ["デ", "ス"], "accent": None, "pos": "助動詞"},
    ]
    assert group_accent_phrases(words3)[0]["accent"] == 3


def test_accent_estimation():
    from app.dsp.yin import yin_f0
    from app.languages.japanese import estimate_accent

    sr = 16000
    n = sr  # 1 s, 4 "moras" of 250 ms
    f = np.concatenate([
        np.full(n // 4, 150.0),   # mora 1 low-ish
        np.full(n // 4, 190.0),   # mora 2 high
        np.full(n // 4, 140.0),   # mora 3 dropped
        np.full(n // 4, 135.0),   # mora 4 low
    ])
    x = np.sin(2 * np.pi * np.cumsum(f) / sr) * 0.5
    analysis = {**yin_f0(x, sr), "duration": 1.0}
    est = estimate_accent(analysis, ["か", "ん", "じ", "き"])
    assert est == 2, f"expected drop after mora 2, got {est}"

    # flat pitch -> heiban
    f0_flat = np.full(n, 160.0)
    x2 = np.sin(2 * np.pi * np.cumsum(f0_flat) / sr) * 0.5
    est2 = estimate_accent({**yin_f0(x2, sr), "duration": 1.0}, ["へ", "い", "ば", "ん"])
    assert est2 == 0, est2


def test_sentence_accent_battery():
    """Composed forms with known-correct Tokyo accents, end to end through
    tokenization → fusion → accent-phrase rules. The expected value is the
    LAST phrase's fall position (0 = flat). Rules documented in
    docs/ja_sentence_pitch_accent.md."""
    from app.languages.japanese import (
        fuse_dictionary_runs, group_accent_phrases, tokenize_sentence,
    )

    cases = [
        # copula
        ("水です", 3), ("雨です", 1), ("水でした", 3), ("水だ", 0),
        ("水だった", 3), ("雨だった", 1), ("水でしょう", 4), ("静かです", 1),
        # ます
        ("行きます", 3), ("食べます", 3), ("行きました", 3), ("行きません", 4),
        ("行きましょう", 4), ("飲みます", 3),
        # て/た (ichidan retract, godan keep, no odaka after flat hosts)
        ("食べた", 1), ("食べて", 1), ("買った", 0), ("分かった", 2),
        ("帰って", 1), ("思った", 2), ("泳いで", 2), ("読んだ", 1),
        ("見て", 1), ("起きて", 1), ("調べて", 2),
        # ない family
        ("食べない", 2), ("買わない", 0), ("走らない", 3),
        ("買わなかった", 3), ("見なかった", 1), ("行かず", 2),
        # たい
        ("食べたい", 3), ("買いたい", 3), ("行きたくない", 3),
        # adjectives
        ("高くない", 2), ("高かった", 2), ("赤かった", 2), ("おいしかった", 3),
        ("高くて", 2), ("よかった", 1),
        # ば / volitional
        ("言えば", 2), ("読めば", 1), ("行こう", 2), ("食べよう", 3),
        # passive/causative
        ("言われた", 0), ("見られた", 2), ("食べさせた", 3),
        # copular negation, そう
        ("学生じゃない", 6), ("元気そう", 4), ("おいしそう", 4),
        # particles
        ("駅まで", 1), ("水まで", 3), ("山の", 0), ("日本の", 0),
        ("猫の", 1), ("犬の", 0), ("山が", 2), ("犬などが", 2),
        # ている / ください
        ("食べている", 1), ("買っている", 0), ("食べています", 5),
        ("見ています", 4), ("待ってください", 1), ("行ってください", 6),
        # サ変 (noun accent survives ます)
        ("勉強します", 6), ("勉強しています", 8),
        # counters
        ("一つ", 2),
    ]
    for text, want in cases:
        phrases = group_accent_phrases(fuse_dictionary_runs(tokenize_sentence(text)))
        assert phrases, text
        got = phrases[-1]["accent"]
        assert got == want, f"{text}: want {want}, got {got} ({''.join(phrases[-1]['moras'])})"


def test_sentence_hints():
    from types import SimpleNamespace

    from app.languages.base import get_module

    module = get_module("ja")
    note = SimpleNamespace(
        expression="見る", reading="みる", sentence="兄は毎日テレビを見ます。",
        pitch_html="", pitch_position=1, pitch_categories="",
        sentence_furigana="兄[あに]は毎日[まいにち]テレビを見[み]ます。",
    )
    acc = module.build_accent_data(note)
    phrases = acc["sentence_phrases"]
    assert phrases[-1]["accent"] == 2, phrases[-1]   # ミマ↓ス
    hints = acc.get("sentence_hints") or []
    assert any("ます" in h for h in hints), hints
    # events are internal — never serialized
    assert all("events" not in p for p in phrases)
    assert all("feat" not in w for w in acc["sentence_words"])

    # flat noun + です: hint explains the fall belongs to です
    note2 = SimpleNamespace(
        expression="水", reading="みず", sentence="これは水です。",
        pitch_html="", pitch_position=0, pitch_categories="", sentence_furigana="",
    )
    acc2 = module.build_accent_data(note2)
    assert acc2["sentence_phrases"][-1]["accent"] == 3   # ミズデ↓ス
    assert any("です" in h for h in acc2.get("sentence_hints") or []), acc2.get("sentence_hints")


if __name__ == "__main__":
    test_yin_and_repair()
    test_smoothing_bridges_short_gaps_only()
    test_alignment_spans()
    test_edge_spur_removal()
    test_alignment_pause_anchoring()
    test_yomitan_pitch_parsing()
    test_srs_rules()
    test_field_matching()
    test_kaishi_pitch_field_parsing()
    test_accent_phrase_grouping()
    test_accent_estimation()
    test_sentence_accent_battery()
    test_sentence_hints()
    print("ALL UNIT TESTS PASSED")
