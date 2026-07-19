"""End-to-end API test: register -> built-in Kaishi deck -> practice loop ->
progress sync -> review. Uses a temp data dir so it never touches real user
data; the Kaishi deck is seeded from resources/ on app import (slow once).
Run:
    ../.venv/Scripts/python -m pytest tests/ -x -q   (or just python tests/test_e2e.py)
"""
import io
import os
import sqlite3
import sys
import tempfile
import zipfile
from pathlib import Path

# Japanese in test output vs Windows cp1252 console
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# isolate data dir BEFORE importing the app
_tmp = tempfile.mkdtemp(prefix="accentier_test_")
os.environ["ACCENTIER_TEST"] = "1"

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app.config as config
config.DATA_DIR = Path(_tmp)
config.DB_PATH = config.DATA_DIR / "test.sqlite3"
config.MEDIA_DIR = config.DATA_DIR / "media"
config.ATTEMPTS_DIR = config.DATA_DIR / "attempts"
config.UPLOADS_DIR = config.DATA_DIR / "uploads"
for d in (config.MEDIA_DIR, config.ATTEMPTS_DIR, config.UPLOADS_DIR):
    d.mkdir(parents=True, exist_ok=True)

from fastapi.testclient import TestClient
from app.main import app
from app.audio import decode_audio

client = TestClient(app)


def auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def register(name: str) -> dict:
    r = client.post("/api/auth/register", json={"username": name, "password": "secret1"})
    assert r.status_code == 200, r.text
    return auth_headers(r.json()["token"])


def scheduled_kaishi_apkg(n_studied: int) -> io.BytesIO:
    """A minimal Kaishi export with the first n notes' cards marked studied.
    Only the collection DB matters for progress sync — media is omitted."""
    import zstandard

    with zipfile.ZipFile(config.KAISHI_APKG) as z:
        db_bytes = zstandard.ZstdDecompressor().decompress(
            z.read("collection.anki21b"), max_output_size=1 << 31)
    db_path = config.DATA_DIR / "sync_src.sqlite"
    db_path.write_bytes(db_bytes)
    conn = sqlite3.connect(db_path)
    nids = [r[0] for r in conn.execute(
        "SELECT DISTINCT nid FROM cards ORDER BY nid LIMIT ?", (n_studied,))]
    conn.executemany("UPDATE cards SET type=2, reps=3 WHERE nid=?", [(n,) for n in nids])
    conn.commit()
    conn.close()

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("collection.anki21b",
                   zstandard.ZstdCompressor().compress(db_path.read_bytes()))
    buf.seek(0)
    return buf


def test_builtin_deck_and_practice_loop():
    h = register("tester")
    assert client.get("/api/auth/me", headers=h).json()["username"] == "tester"
    assert client.post("/api/auth/login", json={"username": "tester", "password": "nope"}).status_code == 401

    # --- the built-in deck is there for every fresh account ---
    decks = client.get("/api/decks", headers=h).json()
    assert len(decks) == 1, decks
    deck = decks[0]
    assert deck["is_builtin"] == 1 and deck["item_count"] == 1500, deck
    deck_id = deck["id"]

    # arbitrary deck upload is gone
    assert client.post("/api/decks/upload", headers=h).status_code in (404, 405)
    # and the shared deck can't be deleted
    assert client.request("DELETE", f"/api/decks/{deck_id}", headers=h).status_code in (404, 405)

    # --- items: full accent coverage from the curated deck field ---
    r = client.get(f"/api/decks/{deck_id}/items", headers=h)
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 1500
    missing = [i for i in items if not i["accent"] or i["accent"].get("accent") is None]
    assert not missing, f"{len(missing)} items without accent"
    assert all(i["accent"]["accent_source"] == "deck" for i in items)
    assert sum(1 for i in items if i.get("word_meaning")) == 1500

    # --- item detail: targets + sentence accent phrases ---
    item = next(i for i in items if i["sentence_audio"] and i["word_audio"])
    r = client.get(f"/api/items/{item['id']}", headers=h)
    assert r.status_code == 200, r.text
    detail = r.json()
    acc = detail["accent"]
    assert acc["moras"] and acc["pattern"] and len(acc["moras"]) == len(acc["pattern"])
    phrases = acc.get("sentence_phrases")
    assert phrases, "sentence should have accent phrases"
    for p in phrases:
        assert len(p["pattern"]) == len(p["moras"])
        assert 0 <= p["accent"] <= len(p["moras"])
    assert "sentence" in detail["targets"], detail["targets"].keys()
    contour = detail["targets"]["sentence"]["contour"]
    assert len([p for p in contour if p[1] is not None]) > 10
    spans = detail["targets"]["sentence"].get("words")
    assert spans, "sentence target should carry estimated word spans"
    dur = detail["targets"]["sentence"]["duration"]
    assert all(0 <= s["start"] <= s["end"] <= dur + 0.05 for s in spans)

    # --- media serving ---
    r = client.get(f"/api/media/{deck_id}/{item['sentence_audio']}", headers=h)
    assert r.status_code == 200 and len(r.content) > 1000

    # --- perfect shadow: the target audio itself scores near-perfect ---
    samples, sr = decode_audio(r.content)
    import soundfile as sf
    buf = io.BytesIO()
    sf.write(buf, samples, sr, format="WAV", subtype="PCM_16")
    buf.seek(0)
    r = client.post(f"/api/items/{item['id']}/attempts", headers=h,
                    files={"audio": ("rec.wav", buf, "audio/wav")}, data={"mode": "sentence"})
    assert r.status_code == 200, r.text
    res = r.json()
    print("perfect-shadow score:", res["result"]["score"], res["result"]["metrics"])
    assert res["result"]["score"] > 90
    assert res["result"]["warp"]
    assert res["srs"]["reps"] == 1 and res["srs"]["outcome"] == "scheduled"

    # --- noise scores low ---
    import numpy as np
    noise = (np.random.default_rng(1).normal(0, 0.1, sr * 2)).astype("float32")
    buf2 = io.BytesIO(); sf.write(buf2, noise, sr, format="WAV", subtype="PCM_16"); buf2.seek(0)
    r = client.post(f"/api/items/{item['id']}/attempts", headers=h,
                    files={"audio": ("rec.wav", buf2, "audio/wav")}, data={"mode": "sentence"})
    assert r.status_code == 200
    bad = r.json()["result"]["score"]
    print("noise score:", bad)
    assert bad < 40

    # --- slice drill: analyzed against a sub-region, never recorded/scheduled ---
    s0, s1 = round(dur * 0.1, 3), round(dur * 0.7, 3)
    buf.seek(0)
    r = client.post(f"/api/items/{item['id']}/attempts", headers=h,
                    files={"audio": ("rec.wav", buf, "audio/wav")},
                    data={"mode": "sentence", "slice_start": str(s0), "slice_end": str(s1)})
    assert r.status_code == 200, r.text
    sl = r.json()
    assert sl["attempt_id"] is None and sl["srs"] is None
    assert sl["result"]["slice"] == [s0, s1]
    buf.seek(0)
    r = client.post(f"/api/items/{item['id']}/attempts", headers=h,
                    files={"audio": ("rec.wav", buf, "audio/wav")},
                    data={"mode": "sentence", "slice_start": "0.0", "slice_end": "0.1"})
    assert r.status_code == 422

    # --- history + stats: slice drills must not appear ---
    hist = client.get(f"/api/items/{item['id']}/attempts", headers=h).json()
    assert len(hist) == 2
    stats = client.get("/api/stats", headers=h).json()
    print("stats:", stats)
    assert stats["total_attempts"] == 2

    # --- per-user isolation on the shared deck ---
    h2 = register("second")
    decks2 = client.get("/api/decks", headers=h2).json()
    assert decks2[0]["id"] == deck_id and decks2[0]["practiced_count"] == 0
    stats2 = client.get("/api/stats", headers=h2).json()
    assert stats2["total_attempts"] == 0

    print("E2E practice loop OK")


def test_progress_sync():
    h = register("syncer")
    deck_id = client.get("/api/decks", headers=h).json()[0]["id"]

    # pristine deck (no scheduling) → helpful error
    with open(config.KAISHI_APKG, "rb") as f:
        r = client.post("/api/progress/sync", headers=h,
                        files={"file": ("Kaishi 1.5k.apkg", f, "application/octet-stream")})
    assert r.status_code == 422 and "scheduling" in r.json()["detail"]

    # an export with 600 studied cards
    buf = scheduled_kaishi_apkg(600)
    r = client.post("/api/progress/sync", headers=h,
                    files={"file": ("my kaishi.apkg", buf, "application/octet-stream")})
    assert r.status_code == 200, r.text
    res = r.json()
    print("sync:", res)
    assert res["studied_notes"] == 600
    assert res["known_count"] == 600

    deck = client.get("/api/decks", headers=h).json()[0]
    assert deck["known_count"] == 600
    items = client.get(f"/api/decks/{deck_id}/items", headers=h).json()["items"]
    assert sum(1 for i in items if i["known"]) == 600

    # other users unaffected
    h2 = register("other")
    assert client.get("/api/decks", headers=h2).json()[0]["known_count"] == 0

    # clear
    r = client.request("DELETE", "/api/progress/sync", headers=h)
    assert r.status_code == 200
    assert client.get("/api/decks", headers=h).json()[0]["known_count"] == 0

    print("progress sync OK")


if __name__ == "__main__":
    test_builtin_deck_and_practice_loop()
    test_progress_sync()
    print("ALL TESTS PASSED")
