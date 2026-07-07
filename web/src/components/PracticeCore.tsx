// The core shadowing loop for one item:
// listen to native audio -> record yourself -> server analyzes -> overlaid
// contours + score + coaching notes. Used by PracticePage, ReviewPage and
// StudyPage.
import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, AttemptResult, ItemDetail, api, mediaUrl } from "../api";
import { Recorder } from "../recorder";
import ContourChart from "./ContourChart";
import { SentencePitchDiagram, WordPitchDiagram } from "./PitchDiagram";
import ScoreRing from "./ScoreRing";

type Phase = "idle" | "recording" | "analyzing";
type Mode = "sentence" | "word";

const SUBSCORE_INFO: [keyof AttemptResult["metrics"], string, string][] = [
  ["shape", "Contour shape", "how closely your melody tracks the native one"],
  ["direction", "Rises & falls", "pitch moving up/down in the right places"],
  ["level", "Pitch range", "size of your movements vs the target"],
  ["timing", "Timing", "matching the native duration"],
];

const SEP_GRAPH_KEY = "accentier_separate_graph";

export default function PracticeCore({
  itemId,
  initialMode,
  onScored,
  footer,
}: {
  itemId: number;
  initialMode?: Mode;
  onScored?: (score: number) => void;
  footer?: React.ReactNode;
}) {
  const [item, setItem] = useState<ItemDetail | null>(null);
  const [mode, setMode] = useState<Mode>("sentence");
  const [phase, setPhase] = useState<Phase>("idle");
  const [result, setResult] = useState<AttemptResult | null>(null);
  const [srsInfo, setSrsInfo] = useState<any>(null);
  const [error, setError] = useState("");
  const [level, setLevel] = useState(0);
  const [elapsed, setElapsed] = useState(0);
  const [playhead, setPlayhead] = useState<number | null>(null);
  const [userPlayhead, setUserPlayhead] = useState<number | null>(null);
  const [userAudioUrl, setUserAudioUrl] = useState<string | null>(null);
  const [separateUser, setSeparateUser] = useState(
    () => localStorage.getItem(SEP_GRAPH_KEY) === "1"
  );

  const recorderRef = useRef<Recorder | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const rafRef = useRef(0);
  const startedAtRef = useRef(0);

  useEffect(() => {
    setItem(null);
    setResult(null);
    setSrsInfo(null);
    setError("");
    setPhase("idle");
    api<ItemDetail>(`/api/items/${itemId}`)
      .then((d) => {
        setItem(d);
        const preferred = initialMode && d.targets[initialMode] ? initialMode : undefined;
        setMode(preferred ?? (d.targets.sentence ? "sentence" : "word"));
      })
      .catch((e: ApiError) => setError(e.message));
    return () => {
      recorderRef.current?.cancel();
      stopPlayback();
      cancelAnimationFrame(rafRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [itemId]);

  useEffect(() => {
    return () => {
      if (userAudioUrl) URL.revokeObjectURL(userAudioUrl);
    };
  }, [userAudioUrl]);

  const target = item?.targets[mode];
  const audioFile = mode === "sentence" ? item?.sentence_audio : item?.word_audio;

  const stopPlayback = () => {
    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current = null;
    }
    setPlayhead(null);
    setUserPlayhead(null);
  };

  const playWithCursor = useCallback(
    (src: string, setCursor: (t: number | null) => void) => {
      stopPlayback();
      cancelAnimationFrame(rafRef.current);
      const audio = new Audio(src);
      audioRef.current = audio;
      audio.play();
      const tick = () => {
        if (audioRef.current === audio && !audio.paused && !audio.ended) {
          setCursor(audio.currentTime);
          rafRef.current = requestAnimationFrame(tick);
        } else {
          setCursor(null);
        }
      };
      rafRef.current = requestAnimationFrame(tick);
    },
    []
  );

  const playTarget = useCallback(() => {
    if (!item || !audioFile) return;
    playWithCursor(mediaUrl(item.deck_id, audioFile), setPlayhead);
  }, [item, audioFile, playWithCursor]);

  const playUserTake = useCallback(() => {
    if (!userAudioUrl) return;
    playWithCursor(userAudioUrl, setUserPlayhead);
  }, [userAudioUrl, playWithCursor]);

  const startRecording = async () => {
    setError("");
    setResult(null);
    stopPlayback();
    try {
      const rec = new Recorder();
      await rec.start();
      recorderRef.current = rec;
      startedAtRef.current = performance.now();
      setPhase("recording");
      const meter = () => {
        if (recorderRef.current) {
          setLevel(recorderRef.current.level());
          setElapsed((performance.now() - startedAtRef.current) / 1000);
          rafRef.current = requestAnimationFrame(meter);
        }
      };
      rafRef.current = requestAnimationFrame(meter);
    } catch (e: any) {
      setError(
        e.name === "NotAllowedError"
          ? "Microphone access denied — allow it in your browser settings."
          : `Could not start recording: ${e.message}`
      );
    }
  };

  const stopRecording = async () => {
    const rec = recorderRef.current;
    if (!rec || !item) return;
    recorderRef.current = null;
    cancelAnimationFrame(rafRef.current);
    setPhase("analyzing");
    try {
      const wav = await rec.stop();
      if (userAudioUrl) URL.revokeObjectURL(userAudioUrl);
      setUserAudioUrl(URL.createObjectURL(wav));
      const form = new FormData();
      form.append("audio", wav, "attempt.wav");
      form.append("mode", mode);
      const res = await api<{ result: AttemptResult; srs: any }>(`/api/items/${item.id}/attempts`, {
        method: "POST",
        body: form,
      });
      setResult(res.result);
      setSrsInfo(res.srs);
      onScored?.(res.result.score);
    } catch (e: any) {
      setError(`Analysis failed: ${e.message}`);
    } finally {
      setPhase("idle");
    }
  };

  if (error && !item) return <div className="error">{error}</div>;
  if (!item) return <span className="spin" />;

  const accent = item.accent;
  const isEstimate = accent?.accent_source === "audio";
  const dueText =
    srsInfo &&
    (srsInfo.outcome === "early"
      ? "extra practice — review schedule unchanged"
      : srsInfo.outcome === "lapse"
      ? "again in 10 minutes"
      : srsInfo.interval_days >= 1
      ? `next ${mode} review in ${Math.round(srsInfo.interval_days)} day${
          Math.round(srsInfo.interval_days) === 1 ? "" : "s"
        }`
      : `next ${mode} review in ${Math.max(1, Math.round(srsInfo.interval_days * 24))}h`);

  return (
    <div className="grid" style={{ gap: 16 }}>
      <div className="panel">
        <div className="practice-head">
          <span className="expression jp">{item.expression}</span>
          {item.reading && <span className="reading jp">{item.reading}</span>}
          {accent?.accent !== null && accent?.accent !== undefined && (
            <span
              className="chip accent"
              title={isEstimate ? "No dictionary entry — accent estimated from the native audio" : undefined}
            >
              [{accent.accent}] {accent.category}
              {isEstimate && " · est."}
            </span>
          )}
          <span className="hint" style={{ marginLeft: "auto" }}>
            {item.deck_name}
          </span>
        </div>
        {item.sentence && (
          <div className="sentence-line jp">
            {item.sentence.split(item.expression).map((part, i, arr) => (
              <span key={i}>
                {part}
                {i < arr.length - 1 && <b>{item.expression}</b>}
              </span>
            ))}
          </div>
        )}

        {accent?.pattern && (
          <div style={{ marginTop: 14 }}>
            <WordPitchDiagram accent={accent} />
          </div>
        )}
        {mode === "sentence" && accent?.sentence_words && accent.sentence_words.some((w) => w.pattern) && (
          <details style={{ marginTop: 12 }}>
            <summary className="hint" style={{ cursor: "pointer" }}>
              Word-by-word accent patterns
            </summary>
            <div style={{ marginTop: 10 }}>
              <SentencePitchDiagram words={accent.sentence_words} />
              <p className="hint" style={{ marginTop: 8 }}>
                Dictionary pattern per word — connected speech merges accent phrases, so treat this as a
                guide to where the drops are, not an exact sentence melody.
              </p>
            </div>
          </details>
        )}
      </div>

      <div className="panel">
        <div style={{ display: "flex", gap: 12, alignItems: "center", marginBottom: 12, flexWrap: "wrap" }}>
          {item.targets.sentence && item.targets.word && (
            <div className="mode-toggle">
              <button
                className={mode === "sentence" ? "on" : ""}
                onClick={() => { setMode("sentence"); setResult(null); stopPlayback(); }}
              >
                Sentence
              </button>
              <button
                className={mode === "word" ? "on" : ""}
                onClick={() => { setMode("word"); setResult(null); stopPlayback(); }}
              >
                Word
              </button>
            </div>
          )}
          <button onClick={playTarget} disabled={!audioFile}>
            ▶ Play native audio
          </button>
          {userAudioUrl && result && (
            <button onClick={playUserTake}>▶ Play your take</button>
          )}
          {result && (
            <label
              className="hint"
              style={{ display: "inline-flex", alignItems: "center", gap: 6, margin: 0, cursor: "pointer" }}
            >
              <input
                type="checkbox"
                checked={separateUser}
                onChange={(e) => {
                  setSeparateUser(e.target.checked);
                  localStorage.setItem(SEP_GRAPH_KEY, e.target.checked ? "1" : "0");
                }}
              />
              my line on its own graph
            </label>
          )}
        </div>

        {target ? (
          <ContourChart
            target={target}
            result={result}
            playheadTime={playhead}
            userPlayheadTime={userPlayhead}
            separateUser={separateUser}
          />
        ) : (
          <p className="hint">No {mode} audio on this card.</p>
        )}

        <div className="record-row">
          {phase !== "analyzing" ? (
            <button
              className={`rec-btn${phase === "recording" ? " recording" : ""}`}
              onClick={phase === "recording" ? stopRecording : startRecording}
              title={phase === "recording" ? "Stop" : "Record your shadow"}
              aria-label={phase === "recording" ? "Stop recording" : "Start recording"}
            >
              <span className="dot" />
            </button>
          ) : (
            <div style={{ width: 64, height: 64, display: "flex", alignItems: "center", justifyContent: "center" }}>
              <span className="spin" />
            </div>
          )}
          {phase === "recording" && (
            <>
              <div className="level-meter">
                <div style={{ width: `${Math.min(100, level * 130)}%` }} />
              </div>
              <span className="hint">{elapsed.toFixed(1)}s — click to stop</span>
            </>
          )}
          {phase === "idle" && !result && (
            <span className="hint">Listen first, then record yourself shadowing the {mode}.</span>
          )}
          {phase === "analyzing" && <span className="hint">Extracting pitch and aligning…</span>}
        </div>
        {error && <div className="error">{error}</div>}
      </div>

      {result && (
        <div className="panel">
          <div className="score-row">
            <ScoreRing score={result.score} />
            <div className="subscores">
              {SUBSCORE_INFO.map(([key, name, desc]) => (
                <div className="subscore" key={key} title={desc}>
                  <span className="name">{name}</span>
                  <div className="bar">
                    <div style={{ width: `${(result.metrics[key] as number) * 100}%` }} />
                  </div>
                  <span className="num">{Math.round((result.metrics[key] as number) * 100)}</span>
                </div>
              ))}
            </div>
          </div>
          <ul className="notes-list">
            {result.notes.map((n, i) => (
              <li key={i}>{n}</li>
            ))}
            {result.divergences.length > 0 && (
              <li>
                {result.divergences.length} divergence region{result.divergences.length > 1 ? "s" : ""} highlighted
                on the chart — hover to compare values.
              </li>
            )}
          </ul>
          {dueText && (
            <p className="hint" style={{ marginTop: 10 }}>
              Progress saved — {dueText}.
            </p>
          )}
        </div>
      )}

      {footer}
    </div>
  );
}
