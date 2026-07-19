// Schematic mora pitch-accent diagram (the "textbook" notation):
// one node per mora on a high or low rail, a step line connecting them,
// and a downward tick where the accent drop happens.
import { AccentData, AccentPhrase, WordAccent } from "../api";

const HIGH_Y = 8;
const LOW_Y = 30;
const STEP = 30;
const LABEL_Y = 50;

function MoraSvg({
  moras,
  pattern,
  accent,
  compact = false,
}: {
  moras: string[];
  pattern: number[];
  accent: number | null;
  compact?: boolean;
}) {
  const n = moras.length;
  if (!n || pattern.length !== n) return null;
  const scale = compact ? 0.72 : 1;
  const width = (n * STEP + 16) * scale;
  const height = (compact ? 42 : 58) * scale;
  const x = (i: number) => (12 + i * STEP + STEP / 2) * scale;
  const y = (h: number) => (h ? HIGH_Y : LOW_Y) * scale;

  const points = pattern.map((h, i) => `${x(i)},${y(h)}`).join(" ");
  // drop marker: between mora `accent-1` and `accent` (1-indexed accent number)
  const dropAfter = accent && accent > 0 && accent <= n ? accent - 1 : null;

  return (
    <svg width={width} height={height} role="img" aria-label={`pitch pattern ${pattern.join("")}`}>
      <polyline points={points} fill="none" stroke="var(--high)" strokeWidth={2 * scale} strokeLinejoin="round" />
      {dropAfter !== null && dropAfter < n - 1 && (
        <line
          x1={(x(dropAfter) + x(dropAfter + 1)) / 2}
          y1={y(1) - 4 * scale}
          x2={(x(dropAfter) + x(dropAfter + 1)) / 2}
          y2={y(0) + 4 * scale}
          stroke="var(--accent)"
          strokeWidth={1.5 * scale}
          strokeDasharray="3 2"
        />
      )}
      {pattern.map((h, i) => (
        <circle
          key={i}
          cx={x(i)}
          cy={y(h)}
          r={(h ? 5 : 4.4) * scale}
          fill={h ? "var(--high)" : "var(--panel)"}
          stroke="var(--high)"
          strokeWidth={1.6 * scale}
        />
      ))}
      {!compact &&
        moras.map((m, i) => (
          <text
            key={i}
            x={x(i)}
            y={LABEL_Y * scale}
            textAnchor="middle"
            fontSize={13 * scale}
            fill="var(--ink-2)"
            className="jp"
          >
            {m}
          </text>
        ))}
    </svg>
  );
}

export function WordPitchDiagram({ accent }: { accent: AccentData }) {
  if (!accent.pattern || !accent.moras.length) return null;
  return (
    <div>
      <MoraSvg moras={accent.moras} pattern={accent.pattern} accent={accent.accent} />
      <div className="hint" style={{ marginTop: 2 }}>
        {accent.category} [{accent.accent}]
        {accent.alternates && accent.alternates.length > 0 && (
          <span style={{ opacity: 0.7 }}> (also [{accent.alternates.join("], [")}])</span>
        )}
        {accent.accent_source && (
          <span style={{ opacity: 0.7 }}>
            {" "}
            · source:{" "}
            {accent.accent_source === "deck"
              ? "Kaishi 1.5k (curated)"
              : accent.accent_source === "kanjium"
              ? "Kanjium"
              : accent.accent_source === "dict"
              ? "pitch dictionary"
              : "estimated from the native audio"}
          </span>
        )}
      </div>
    </div>
  );
}

// Full-sentence melody: accent phrases on a shared vertical scale. Each
// accented phrase pushes the following phrases' high plateau down one notch
// (downstep); punctuation resets the height. The low rail is shared.
const PHRASE_STEP = 24;
const PHRASE_HIGH_BY_LEVEL = [10, 17, 23, 28];
const PHRASE_LOW_Y = 42;
const PHRASE_MORA_Y = 60;
const PHRASE_WORD_Y = 76;

function PhraseSvg({ phrase }: { phrase: AccentPhrase }) {
  const n = phrase.moras.length;
  if (!n || phrase.pattern.length !== n) return null;
  const width = n * PHRASE_STEP + 10;
  const highY = PHRASE_HIGH_BY_LEVEL[Math.min(phrase.level, PHRASE_HIGH_BY_LEVEL.length - 1)];
  const x = (i: number) => 5 + i * PHRASE_STEP + PHRASE_STEP / 2;
  const y = (h: number) => (h ? highY : PHRASE_LOW_Y);
  const points = phrase.pattern.map((h, i) => `${x(i)},${y(h)}`).join(" ");
  const acc = phrase.accent;
  // the drop tick: after mora `acc` (1-indexed); for odaka it sits at the
  // phrase edge — the fall lands on whatever follows
  const dropX = acc > 0 && acc <= n ? (acc < n ? (x(acc - 1) + x(acc)) / 2 : x(n - 1) + PHRASE_STEP / 2) : null;
  return (
    <svg width={width} height={PHRASE_WORD_Y + 10} role="img"
         aria-label={`accent phrase ${phrase.surface} pattern ${phrase.pattern.join("")}`}>
      <polyline points={points} fill="none" stroke="var(--high)" strokeWidth={2} strokeLinejoin="round" />
      {dropX !== null && (
        <line x1={dropX} y1={highY - 4} x2={dropX} y2={PHRASE_LOW_Y + 4}
              stroke="var(--accent)" strokeWidth={1.5} strokeDasharray="3 2" />
      )}
      {phrase.pattern.map((h, i) => (
        <circle key={i} cx={x(i)} cy={y(h)} r={h ? 4.6 : 4}
                fill={h ? "var(--high)" : "var(--panel)"} stroke="var(--high)" strokeWidth={1.5} />
      ))}
      {phrase.moras.map((m, i) => (
        <text key={i} x={x(i)} y={PHRASE_MORA_Y} textAnchor="middle" fontSize={12}
              fill="var(--ink-2)" className="jp">
          {m}
        </text>
      ))}
      {phrase.words.map((w, i) => (
        <text key={i} x={x(w.at) - PHRASE_STEP / 2 + 2} y={PHRASE_WORD_Y} textAnchor="start"
              fontSize={11} fill="var(--ink-3)" className="jp">
          {w.surface}
        </text>
      ))}
    </svg>
  );
}

export function SentencePhraseDiagram({ phrases }: { phrases: AccentPhrase[] }) {
  const drawable = phrases.filter((p) => p.moras.length > 0);
  if (!drawable.length) return null;
  return (
    <div style={{ display: "flex", alignItems: "flex-start", flexWrap: "wrap", rowGap: 6 }}>
      {drawable.map((p, i) => (
        <div key={i} style={{ display: "flex", alignItems: "flex-start" }}>
          <PhraseSvg phrase={p} />
          {p.break_after && i < drawable.length - 1 && (
            <span style={{ color: "var(--ink-3)", margin: "26px 10px 0 6px", fontSize: 13 }}>‖</span>
          )}
        </div>
      ))}
    </div>
  );
}

export function SentencePitchDiagram({ words }: { words: WordAccent[] }) {
  const drawable = words.filter((w) => w.moras.length > 0);
  if (!drawable.length) return null;
  return (
    <div className="word-diagrams">
      {drawable.map((w, i) =>
        w.pattern ? (
          <div key={i} className="word-diagram">
            <MoraSvg moras={w.moras} pattern={w.pattern} accent={w.accent} compact />
            <div className="surface jp">{w.surface}</div>
          </div>
        ) : (
          <div key={i} className="word-diagram">
            <div style={{ height: 30, display: "flex", alignItems: "flex-end", justifyContent: "center" }}>
              <span style={{ color: "var(--ink-3)", fontSize: 11 }}>?</span>
            </div>
            <div className="surface jp" style={{ opacity: 0.6 }}>
              {w.surface}
            </div>
          </div>
        )
      )}
    </div>
  );
}
