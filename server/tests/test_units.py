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


def test_srs_rules():
    from app.db import init_db, get_conn
    from app import srs

    init_db()
    c = get_conn()
    c.execute("INSERT OR IGNORE INTO users VALUES (1,'u','x',0)")
    c.execute("INSERT OR IGNORE INTO decks (id,user_id,name,language,created_at) VALUES (1,1,'d','ja',0)")
    c.execute("INSERT OR IGNORE INTO items (id,deck_id,expression,created_at) VALUES (1,1,'e',0)")
    c.commit()

    r1 = srs.record_result(1, 1, "sentence", 90)
    assert r1["outcome"] == "scheduled" and r1["reps"] == 1
    # immediate re-practice: schedule must not balloon
    r2 = srs.record_result(1, 1, "sentence", 95)
    r3 = srs.record_result(1, 1, "sentence", 95)
    assert r2["outcome"] == "early" and r3["outcome"] == "early"
    assert r3["interval_days"] == r1["interval_days"]
    # word mode is a separate schedule
    w = srs.record_result(1, 1, "word", 90)
    assert w["outcome"] == "scheduled" and w["reps"] == 1
    # a bad take lapses even between reviews
    r4 = srs.record_result(1, 1, "sentence", 20)
    assert r4["outcome"] == "lapse" and r4["reps"] == 0 and r4["lapses"] == 1


def test_field_matching():
    from app.apkg import _build_field_index

    idx = _build_field_index(["Sentence Audio", "Word-Audio", "Expression", "読み", "Pitch Accent"])
    assert idx["sentence_audio"] == 0
    assert idx["word_audio"] == 1
    assert idx["expression"] == 2
    assert idx["reading"] == 3
    assert idx["pitch_position"] == 4


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


if __name__ == "__main__":
    test_yin_and_repair()
    test_smoothing_bridges_short_gaps_only()
    test_alignment_spans()
    test_srs_rules()
    test_field_matching()
    test_accent_estimation()
    print("ALL UNIT TESTS PASSED")
