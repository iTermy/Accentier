// Pitch contour comparison chart (canvas).
// Target = blue line on its own timeline; user attempt = orange line,
// pre-warped onto the target timeline by the server's DTW so the two
// overlay directly. Divergence regions arrive as [start,end] bands.
// Hover shows a crosshair with both semitone values at that instant.
import { useEffect, useMemo, useRef, useState } from "react";
import { AttemptResult, TargetAnalysis } from "../api";

const COLORS = {
  target: "#3987e5",
  user: "#d95926",
  band: "rgba(250, 178, 25, 0.13)",
  bandEdge: "rgba(250, 178, 25, 0.35)",
  grid: "#262c38",
  axis: "#3a4150",
  ink: "#a8afbd",
};

type Pt = [number, number | null];

function buildLookup(points: [number, number][]): (t: number) => number | null {
  return (t: number) => {
    if (!points.length) return null;
    let best: [number, number] | null = null;
    let bestD = 0.04; // 40 ms window
    for (const p of points) {
      const d = Math.abs(p[0] - t);
      if (d < bestD) {
        bestD = d;
        best = p;
      }
    }
    return best ? best[1] : null;
  };
}

export default function ContourChart({
  target,
  result,
  playheadTime,
}: {
  target: TargetAnalysis;
  result: AttemptResult | null;
  playheadTime?: number | null;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(800);
  const [hover, setHover] = useState<{ px: number; t: number } | null>(null);
  const height = 240;
  const pad = { l: 42, r: 14, t: 14, b: 26 };

  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setWidth(el.clientWidth));
    ro.observe(el);
    setWidth(el.clientWidth);
    return () => ro.disconnect();
  }, []);

  const duration = target.duration;
  const { yMin, yMax } = useMemo(() => {
    const vals: number[] = [];
    for (const [, v] of target.contour) if (v !== null) vals.push(v);
    if (result) for (const [, v] of result.aligned_user) vals.push(v);
    if (!vals.length) return { yMin: -6, yMax: 6 };
    const lo = Math.min(...vals), hi = Math.max(...vals);
    const padV = Math.max(1.5, (hi - lo) * 0.15);
    return { yMin: lo - padV, yMax: hi + padV };
  }, [target, result]);

  const tx = (t: number) => pad.l + (t / duration) * (width - pad.l - pad.r);
  const ty = (v: number) => pad.t + (1 - (v - yMin) / (yMax - yMin)) * (height - pad.t - pad.b);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const dpr = window.devicePixelRatio || 1;
    canvas.width = width * dpr;
    canvas.height = height * dpr;
    canvas.style.width = `${width}px`;
    canvas.style.height = `${height}px`;
    const ctx = canvas.getContext("2d")!;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, height);

    // divergence bands first (under everything)
    if (result) {
      for (const d of result.divergences) {
        const x0 = tx(d.start), x1 = tx(d.end);
        ctx.fillStyle = COLORS.band;
        ctx.fillRect(x0, pad.t, x1 - x0, height - pad.t - pad.b);
        ctx.strokeStyle = COLORS.bandEdge;
        ctx.lineWidth = 1;
        ctx.strokeRect(x0 + 0.5, pad.t + 0.5, x1 - x0 - 1, height - pad.t - pad.b - 1);
      }
    }

    // grid: semitone lines every 3 st
    ctx.strokeStyle = COLORS.grid;
    ctx.lineWidth = 1;
    ctx.fillStyle = COLORS.ink;
    ctx.font = "11px system-ui";
    ctx.textAlign = "right";
    const stStep = yMax - yMin > 18 ? 6 : 3;
    for (let v = Math.ceil(yMin / stStep) * stStep; v <= yMax; v += stStep) {
      const y = ty(v);
      ctx.beginPath();
      ctx.moveTo(pad.l, y);
      ctx.lineTo(width - pad.r, y);
      ctx.stroke();
      ctx.fillText(`${v > 0 ? "+" : ""}${v}`, pad.l - 7, y + 3.5);
    }
    // x ticks every 0.5s
    ctx.textAlign = "center";
    const tickStep = duration > 6 ? 1 : 0.5;
    for (let t = 0; t <= duration + 1e-6; t += tickStep) {
      ctx.fillText(`${t.toFixed(1)}s`, tx(t), height - 8);
    }
    // axis label
    ctx.save();
    ctx.translate(11, height / 2);
    ctx.rotate(-Math.PI / 2);
    ctx.fillText("semitones vs speaker median", 0, 0);
    ctx.restore();

    const drawContour = (pts: Pt[], color: string, widthPx: number) => {
      ctx.strokeStyle = color;
      ctx.lineWidth = widthPx;
      ctx.lineJoin = "round";
      ctx.lineCap = "round";
      let open = false;
      ctx.beginPath();
      let lastT: number | null = null;
      for (const [t, v] of pts) {
        if (v === null || (lastT !== null && t - lastT > 0.06)) {
          if (v === null) {
            lastT = null;
            open = false;
            continue;
          }
          open = false;
        }
        const x = tx(t), y = ty(v);
        if (!open) {
          ctx.moveTo(x, y);
          open = true;
        } else {
          ctx.lineTo(x, y);
        }
        lastT = t;
      }
      ctx.stroke();
    };

    drawContour(target.contour, COLORS.target, 2.2);
    if (result) drawContour(result.aligned_user as unknown as Pt[], COLORS.user, 2);

    // playhead during audio playback
    if (playheadTime != null && playheadTime >= 0 && playheadTime <= duration) {
      const x = tx(playheadTime);
      ctx.strokeStyle = "rgba(255,255,255,0.5)";
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(x, pad.t);
      ctx.lineTo(x, height - pad.b);
      ctx.stroke();
    }

    // hover crosshair
    if (hover) {
      ctx.strokeStyle = "rgba(255,255,255,0.25)";
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(hover.px, pad.t);
      ctx.lineTo(hover.px, height - pad.b);
      ctx.stroke();
    }
  }, [width, target, result, hover, playheadTime, yMin, yMax]);

  const targetLookup = useMemo(
    () => buildLookup(target.contour.filter((p): p is [number, number] => p[1] !== null)),
    [target]
  );
  const userLookup = useMemo(
    () => (result ? buildLookup(result.aligned_user) : null),
    [result]
  );

  const onMove = (e: React.MouseEvent) => {
    const rect = canvasRef.current!.getBoundingClientRect();
    const px = e.clientX - rect.left;
    if (px < pad.l || px > width - pad.r) return setHover(null);
    const t = ((px - pad.l) / (width - pad.l - pad.r)) * duration;
    setHover({ px, t });
  };

  const hoverTarget = hover ? targetLookup(hover.t) : null;
  const hoverUser = hover && userLookup ? userLookup(hover.t) : null;

  return (
    <div className="contour-wrap" ref={wrapRef}>
      <div className="diagram-legend">
        <span className="key">
          <span className="swatch" style={{ background: COLORS.target }} /> native audio
        </span>
        {result && (
          <span className="key">
            <span className="swatch" style={{ background: COLORS.user }} /> you (time-aligned)
          </span>
        )}
        {result && result.divergences.length > 0 && (
          <span className="key">
            <span className="swatch" style={{ background: "rgba(250,178,25,0.6)" }} /> divergence
          </span>
        )}
      </div>
      <canvas
        ref={canvasRef}
        onMouseMove={onMove}
        onMouseLeave={() => setHover(null)}
        style={{ display: "block", width: "100%" }}
      />
      {hover && (hoverTarget !== null || hoverUser !== null) && (
        <div className="contour-tooltip" style={{ left: hover.px, top: 30 }}>
          {hover.t.toFixed(2)}s
          {hoverTarget !== null && (
            <>
              {" · "}native <b style={{ color: COLORS.target }}>{hoverTarget.toFixed(1)}</b>
            </>
          )}
          {hoverUser !== null && (
            <>
              {" · "}you <b style={{ color: COLORS.user }}>{hoverUser.toFixed(1)}</b>
            </>
          )}{" "}
          st
        </div>
      )}
    </div>
  );
}
