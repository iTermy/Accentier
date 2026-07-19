"""Central paths and constants."""
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = SERVER_DIR / "data"
DB_PATH = DATA_DIR / "accentier.sqlite3"
MEDIA_DIR = DATA_DIR / "media"          # extracted deck audio: media/<deck_id>/<filename>
ATTEMPTS_DIR = DATA_DIR / "attempts"    # user recordings: attempts/<attempt_id>.wav
UPLOADS_DIR = DATA_DIR / "uploads"      # temp storage for incoming .apkg files
PITCH_DICTS_DIR = DATA_DIR / "pitch_dicts"  # optional user-supplied Yomitan pitch dict zips
VENDOR_DIR = SERVER_DIR / "vendor"
KANJIUM_PATH = VENDOR_DIR / "kanjium_accents.txt"
KAISHI_APKG = SERVER_DIR.parent / "resources" / "Kaishi 1.5k.apkg"
WEB_DIST = SERVER_DIR.parent / "web" / "dist"

# Analysis parameters
ANALYSIS_SR = 16000        # everything is resampled to this before DSP
F0_HOP = 160               # 10 ms hop at 16 kHz
F0_FRAME = 1024            # 64 ms window
F0_MIN = 60.0
F0_MAX = 500.0

for d in (DATA_DIR, MEDIA_DIR, ATTEMPTS_DIR, UPLOADS_DIR, PITCH_DICTS_DIR):
    d.mkdir(parents=True, exist_ok=True)
