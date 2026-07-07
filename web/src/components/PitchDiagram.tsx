// Schematic mora pitch-accent diagram (the "textbook" notation):
// one node per mora on a high or low rail, a step line connecting them,
// and a downward tick where the accent drop happens.
import { AccentData, WordAccent } from "../api";

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
        {accent.accent_source && (
          <span style={{ opacity: 0.7 }}>
            {" "}
            · source: {accent.accent_source === "deck" ? "your deck (Yomitan)" : "Kanjium"}
          </span>
        )}
      </div>
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
