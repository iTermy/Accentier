"""End-to-end API test: register -> upload real deck -> practice loop -> review.

Uses a temp data dir so it never touches real user data. Run:
    ../.venv/Scripts/python -m pytest tests/ -x -q   (or just python tests/test_e2e.py)
"""
import io
import os
import sys
import tempfile
from pathlib import Path

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
from app.audio import decode_audio, save_wav

RESOURCES = Path(__file__).resolve().parent.parent.parent / "resources"
JP_DECK = RESOURCES / "JPAnimeMining.apkg"
IT_DECK = RESOURCES / "Italian Mining.apkg"

client = TestClient(app)


def auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_full_loop():
    # --- register / login ---
    r = client.post("/api/auth/register", json={"username": "tester", "password": "secret1"})
    assert r.status_code == 200, r.text
    token = r.json()["token"]
    h = auth_headers(token)
    assert client.get("/api/auth/me", headers=h).json()["username"] == "tester"
    # wrong password rejected
    assert client.post("/api/auth/login", json={"username": "tester", "password": "nope"}).status_code == 401

    # --- upload Italian deck (smaller; exercises generic module) ---
    with open(IT_DECK, "rb") as f:
        r = client.post("/api/decks/upload", headers=h,
                        files={"file": ("Italian Mining.apkg", f, "application/octet-stream")},
                        data={"language": "auto"})
    assert r.status_code == 200, r.text
    up = r.json()
    print("upload:", up)
    assert up["language"] == "generic"
    assert up["items_imported"] > 100
    deck_id = up["deck_id"]

    # --- items list ---
    r = client.get(f"/api/decks/{deck_id}/items", headers=h)
    assert r.status_code == 200
    items = r.json()["items"]
    item = next(i for i in items if i["sentence_audio"])

    # --- item detail computes target contour ---
    r = client.get(f"/api/items/{item['id']}", headers=h)
    assert r.status_code == 200, r.text
    detail = r.json()
    assert "sentence" in detail["targets"], detail["targets"].keys()
    contour = detail["targets"]["sentence"]["contour"]
    voiced_pts = [p for p in contour if p[1] is not None]
    assert len(voiced_pts) > 10, "target contour should have voiced frames"

    # --- media serving ---
    r = client.get(f"/api/media/{deck_id}/{item['sentence_audio']}", headers=h)
    assert r.status_code == 200 and len(r.content) > 1000

    # --- submit attempt: use the target audio itself as the 'recording' (perfect shadow) ---
    samples, sr = decode_audio(r.content)
    buf = io.BytesIO()
    import soundfile as sf
    sf.write(buf, samples, sr, format="WAV", subtype="PCM_16")
    buf.seek(0)
    r = client.post(f"/api/items/{item['id']}/attempts", headers=h,
                    files={"audio": ("rec.wav", buf, "audio/wav")}, data={"mode": "sentence"})
    assert r.status_code == 200, r.text
    res = r.json()
    print("perfect-shadow score:", res["result"]["score"], res["result"]["metrics"])
    assert res["result"]["score"] > 90, "identical audio should score near-perfect"
    assert res["srs"]["reps"] == 1

    # --- a garbage attempt (noise) scores low ---
    import numpy as np
    noise = (np.random.default_rng(1).normal(0, 0.1, sr * 2)).astype("float32")
    buf2 = io.BytesIO(); sf.write(buf2, noise, sr, format="WAV", subtype="PCM_16"); buf2.seek(0)
    r = client.post(f"/api/items/{item['id']}/attempts", headers=h,
                    files={"audio": ("rec.wav", buf2, "audio/wav")}, data={"mode": "sentence"})
    assert r.status_code == 200
    bad = r.json()["result"]["score"]
    print("noise score:", bad)
    assert bad < 40

    # --- history + review queue + stats ---
    hist = client.get(f"/api/items/{item['id']}/attempts", headers=h).json()
    assert len(hist) == 2
    queue = client.get("/api/review/queue", headers=h).json()
    # last attempt was noise -> lapse -> due in 10 min, so not in queue yet unless failed... lapse sets due +600s
    stats = client.get("/api/stats", headers=h).json()
    print("stats:", stats)
    assert stats["total_attempts"] == 2

    print("E2E OK")


def test_japanese_deck_import():
    r = client.post("/api/auth/register", json={"username": "jp", "password": "secret1"})
    h = auth_headers(r.json()["token"])
    with open(JP_DECK, "rb") as f:
        r = client.post("/api/decks/upload", headers=h,
                        files={"file": ("JPAnimeMining.apkg", f, "application/octet-stream")},
                        data={"language": "auto"})
    assert r.status_code == 200, r.text
    up = r.json()
    print("JP upload:", up)
    assert up["language"] == "ja"
    assert up["items_imported"] >= 890

    items = client.get(f"/api/decks/{up['deck_id']}/items", headers=h).json()["items"]
    with_accent = [i for i in items if i["accent"] and i["accent"].get("accent") is not None]
    print(f"items with accent number: {len(with_accent)}/{len(items)}")
    assert len(with_accent) > 700  # deck pitch + kanjium fallback should beat deck-only 653

    # item detail with accent diagram + sentence words
    item = next(i for i in with_accent if i["sentence_audio"])
    d = client.get(f"/api/items/{item['id']}", headers=h).json()
    acc = d["accent"]
    assert acc["moras"] and acc["pattern"] and len(acc["moras"]) == len(acc["pattern"])
    assert any(w.get("pattern") for w in acc.get("sentence_words", []))
    print("sample item:", d["expression"], d["reading"], acc["accent"], acc["category"],
          "source:", acc["accent_source"])


if __name__ == "__main__":
    test_full_loop()
    test_japanese_deck_import()
    print("ALL TESTS PASSED")
