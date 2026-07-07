# Accentier

Shadowing practice with real pitch feedback, built around your Anki immersion workflow.
Upload your mining deck, listen to the native audio you mined, record yourself shadowing
it, and see your pitch contour overlaid on the native one — with divergences highlighted,
a score, and an SRS schedule that brings items back for review.

Initial focus: **Japanese pitch accent**. The analysis layer is language-modular; the
included Italian deck runs in a generic intonation-contour mode through the same pipeline.

## Quickstart

Backend (Python 3.13, venv already at repo root):

```powershell
.venv\Scripts\python -m pip install -r server\requirements.txt
cd server
..\.venv\Scripts\python -m uvicorn app.main:app --port 8000
```

Frontend (built once, then served by the backend at http://127.0.0.1:8000):

```powershell
cd web
npm install
npm run build
```

For frontend development instead: `npm run dev` (Vite on :5173, proxies `/api` to :8000).

Then open **http://127.0.0.1:8000**, register an account, and drop an `.apkg`
(export from Anki with *"Include media"* checked) onto the dashboard.

Tests (run against the real decks in `/resources`):

```powershell
cd server
..\.venv\Scripts\python tests\test_e2e.py
```

## Architecture

```
server/  FastAPI + SQLite — API, DSP, language modules, serves web/dist
  app/
    apkg.py           .apkg parser (modern zstd/protobuf format AND legacy schema-11)
    audio.py          decode (mp3/wav/ogg/flac via libsndfile) → mono 16 kHz
    dsp/yin.py        YIN F0 tracker (NumPy implementation of the 2002 paper)
    dsp/align.py      DTW alignment + divergence scoring between contours
    languages/        LanguageModule interface; japanese.py, generic.py
    kanjium.py        Kanjium pitch-accent DB loader (vendored, 124k entries)
    analysis.py       target/attempt analysis orchestration + caching
    srs.py            SM-2-style scheduler driven by shadowing scores
    routers/          auth, decks/upload/media, items/attempts/review/stats
  vendor/kanjium_accents.txt   pitch-accent ground truth (see below)
  data/               runtime: SQLite DB, extracted media, recordings (gitignored)
web/     Vite + React + TS — canvas contour chart, SVG mora diagrams, recorder
```

### How the hard parts work (none are faked)

**F0 extraction** — `dsp/yin.py` implements YIN (de Cheveigné & Kawahara 2002):
FFT-autocorrelation difference function, cumulative-mean normalization, absolute-
threshold dip selection, parabolic interpolation, energy-gated voicing, median
smoothing and octave-glitch removal. Verified against synthetic tones (±0.7 Hz on
vibrato), and produces plausible tracks on the deck's anime audio (speaker medians
98–380 Hz). 10 ms hop, 64 ms window, 60–500 Hz range.

**Speaker normalization** — contours are converted to *semitones relative to each
speaker's own median voiced pitch*, so a low-voiced learner shadowing a high-voiced
seiyuu is scored on melody, not register. (Verified: a +6 st transposed copy of the
target scores 100.)

**Alignment & scoring** — `dsp/align.py` runs DTW (Sakoe-Chiba band) over
(semitone, slope) features; the slope term keeps rises aligned to rises. Four
explainable subscores — contour shape (correlation), rises & falls (slope-sign
agreement), pitch range (mean |Δst|), timing (duration ratio) — are combined with
per-language weights. Divergence regions are maximal runs where |Δst| > 2.8 for
≥ 90 ms, drawn as bands on the target timeline. Sanity: identical audio → 100,
tempo-shifted noisy copy → 96, a different sentence → ~50, noise/silence → 0.

**Target accent data** — three tiers, best available wins:
1. the deck's own Yomitan `PitchPosition` field (653/900 notes in the JP test deck),
2. the vendored **Kanjium** accent database (open source, the same data Yomitan
   pitch dictionaries are built from), keyed by surface+reading with lemma fallback
   via the UniDic tokenizer — lifts coverage to 782/900,
3. always: the native audio's own F0 contour, which is what the recording is
   actually compared against. The dictionary data drives the schematic mora diagram;
   the audio drives the score.

**.apkg parsing** — handles the current Anki export format (zstd-compressed
`collection.anki21b`, protobuf media map — parsed with a minimal built-in varint
reader, no protobuf dependency) and the legacy schema-11 format (plain SQLite +
JSON media map). Field roles (expression/reading/sentence/audio/pitch) are detected
by name across Lapis/JPMN/Yomitan-style note types, with a `[sound:...]`-scan
heuristic as fallback for unknown note types.

**Recording** — the browser captures raw PCM via an AudioWorklet and encodes WAV
client-side. No MediaRecorder, no codecs, nothing lossy between the mic and the
pitch tracker.

### Language modules

`languages/base.py` defines the interface: a module owns target-pattern derivation
(`build_accent_data`), score weighting, and feedback wording. The DSP layer is
language-neutral. `japanese.py` adds mora segmentation, accent-number → H/L pattern
rules, Kanjium/deck lookups, and pitch-accent-specific coaching. `generic.py` is the
contour-only fallback (used by the Italian deck) and the blueprint for future modules
(English stress placement, Mandarin tones, …). Upload auto-detects language by script.

### Persistence & review

SQLite: users (PBKDF2 passwords, bearer-token sessions), decks, items (with cached
target analysis), attempts (score + full feedback JSON + the recording), and SRS
state. The scheduler is SM-2-lite where quality comes from the shadowing score
instead of self-grading: ≥85 grows the interval fast, <55 lapses the item back to
10 minutes. The dashboard surfaces the due queue; Review mode walks through it.

## Known limitations / deferred (deliberately)

- **Sentence pitch diagram is word-by-word dictionary patterns**, not a true
  sentence melody — accent-phrase merging and downstep are not modeled (labeled as
  such in the UI). The *audio contour* target is unaffected; this only concerns the
  schematic diagram.
- **Divergences are time-anchored, not mora-anchored.** Naming the exact word/mora
  you missed needs forced alignment (text↔audio), the top candidate for the next
  iteration.
- Word-mode audio (Yomitan dictionary audio) and sentence-mode audio are analyzed
  identically; there's no per-word extraction from sentence audio.
- Auth is minimal: sessions never expire, no rate limiting, no password reset.
- Re-uploading a deck creates a new deck (no merge/dedupe); attempt WAVs accumulate
  with no cleanup job.
- Legacy .apkg support is tested against a synthetic deck, not a real old export.
- Kanjium data is vendored as-is; homograph disambiguation takes the first listed
  accent when the deck doesn't specify one.

## Data credits

- [Kanjium](https://github.com/mifunetoshiro/kanjium) pitch-accent database.
- [UniDic](https://clrd.ninjal.ac.jp/unidic/) via `fugashi` + `unidic-lite` for
  tokenization and readings.
