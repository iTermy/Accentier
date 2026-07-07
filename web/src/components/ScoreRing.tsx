export default function ScoreRing({ score }: { score: number }) {
  const r = 44;
  const c = 2 * Math.PI * r;
  const frac = Math.max(0, Math.min(1, score / 100));
  const color = score >= 85 ? "var(--good)" : score >= 60 ? "var(--warn)" : "var(--accent)";
  return (
    <svg width={110} height={110} role="img" aria-label={`score ${Math.round(score)} out of 100`}>
      <circle cx={55} cy={55} r={r} fill="none" stroke="var(--panel-2)" strokeWidth={9} />
      <circle
        cx={55}
        cy={55}
        r={r}
        fill="none"
        stroke={color}
        strokeWidth={9}
        strokeLinecap="round"
        strokeDasharray={`${c * frac} ${c}`}
        transform="rotate(-90 55 55)"
      />
      <text x={55} y={60} textAnchor="middle" fontSize={26} fontWeight={650} fill="var(--ink)">
        {Math.round(score)}
      </text>
      <text x={55} y={76} textAnchor="middle" fontSize={11} fill="var(--ink-3)">
        / 100
      </text>
    </svg>
  );
}
