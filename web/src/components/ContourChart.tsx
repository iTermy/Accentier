// Pitch contour comparison chart (canvas).
// Target = blue line on its own timeline; user attempt = orange line, either
// pre-warped onto the target timeline (overlay) or on its own panel below
// with its own timeline (separate). Divergence regions arrive as [start,end]
// bands on the target timeline. Estimated word/mora spans are labeled in a
// band at the top. Playheads track both native-audio and your-take playback;
// in overlay mode the user playhead is mapped through the DTW warp so it
// rides the target timeline.
import { useEffect, useMemo, useRef, useState } from "react";
import { AttemptResult, TargetAnalysis, WordSpan } from "../api";

const COLORS = {
  target: "#3987e5",
  user: "#d95926",
  band: "rgba(250, 178, 25, 0.13)",
  bandEdge: "rgba(250, 178, 25, 0.35)",
  grid: "#262c38",
  wordTick: "rgba(255,255,255,0.09)",
  ink: "#a8afbd",
  inkFaint: "#6b7280",
};

const JP_FONT = "12px 'Yu Gothic UI', 'Hiragino Kaku Gothic ProN', Meiryo, sans-serif";

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

/** Map a user-take time to target time via the sparse DTW warp pairs. */
function warpToTarget(warp: [number, number][], u: number): number | null {
  if (!warp.length) return null;
  if (u <= warp[0][0]) return warp[0][1];
  if (u >= warp[warp.length - 1][0]) return warp[warp.length - 1][1];
  for (let i = 1; i < warp.length; i++) {
    if (warp[i][0] >= u) {
      const [u0, t0] = warp[i - 1];
      const [u1, t1] = warp[i];
      if (u1 === u0) return t0;
      return t0 + ((u - u0) / (u1 - u0)) * (t1 - t0);
    }
  }
  return null;
}

function drawContourPath(
  ctx: CanvasRenderingContext2D,
  pts: Pt[],
  tx: (t: number) => number,
  ty: (v: number) => number,
  color: string,
  widthPx: number
) {
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
}

function drawPlayhead(
  ctx: CanvasRenderingContext2D,
  x: number,
  top: number,
  bottom: number,
  color: string
) {
  ctx.strokeStyle = color;
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  ctx.moveTo(x, top);
  ctx.lineTo(x, bottom);
  ctx.stroke();
  ctx.fillStyle = color;
  ctx.beginPath();
  ctx.moveTo(x - 4, top);
  ctx.lineTo(x + 4, top);
  ctx.lineTo(x, top + 5);
  ctx.closePath();
  ctx.fill();
}

export default function ContourChart({
  target,
  result,
  playheadTime,
  userPlayheadTime,
  separateUser = false,
}: {
  target: TargetAnalysis;
  result: AttemptResult | null;
  playheadTime?: number | null;
  userPlayheadTime?: number | null;
  separateUser?: boolean;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const userCanvasRef = useRef<HTMLCanvasElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(800);
  const [hover, setHover] = useState<{ px: number; t: number } | null>(null);

  const spans: WordSpan[] | undefined = target.words || target.moras;
  const wordBand = spans && spans.length > 0 ? 22 : 0;
  const height = 240 + wordBand;
  const userHeight = 150;
  const pad = { l: 42, r: 14, t: 14 + wordBand, b: 26 };

  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setWidth(el.clientWidth));
    ro.observe(el);
    setWidth(el.clientWidth);
    return () => ro.disconnect();
  }, []);

  const duration = target.duration;
  const showUserOverlay = !!result && !separateUser;

  const userDuration = useMemo(() => {
    if (!result) return 0;
    if (result.user_duration) return result.user_duration;
    const uc = result.user_contour;
    return uc.length ? uc[uc.length - 1][0] : 0;
  }, [result]);

  // shared y-range across both panels so the same drop looks the same size
  const { yMin, yMax } = useMemo(() => {
    const vals: number[] = [];
    for (const [, v] of target.contour) if (v !== null) vals.push(v);
    if (result) for (const [, v] of result.user_contour) if (v !== null) vals.push(v);
    if (!vals.length) return { yMin: -6, yMax: 6 };
    const lo = Math.min(...vals), hi = Math.max(...vals);
    const padV = Math.max(1.5, (hi - lo) * 0.15);
    return { yMin: lo - padV, yMax: hi + padV };
  }, [target, result]);

  const tx = (t: number) => pad.l + (t / duration) * (width - pad.l - pad.r);
  const ty = (v: number) => pad.t + (1 - (v - yMin) / (yMax - yMin)) * (height - pad.t - pad.b);

  // ---- main (target) panel ----
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

    // grid: semitone lines
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
    // x ticks
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

    // estimated word/mora spans: boundary ticks + labels in the top band
    if (spans && spans.length) {
      ctx.strokeStyle = COLORS.wordTick;
      ctx.lineWidth = 1;
      for (const s of spans.slice(1)) {
        const x = tx(s.start);
        ctx.beginPath();
        ctx.moveTo(x, pad.t - 4);
        ctx.lineTo(x, height - pad.b);
        ctx.stroke();
      }
      ctx.font = JP_FONT;
      ctx.textAlign = "center";
      for (const s of spans) {
        const x0 = tx(s.start), x1 = tx(s.end);
        const label =
          s.accent !== undefined && s.accent !== null ? `${s.surface}[${s.accent}]` : s.surface;
        const w = ctx.measureText(label).width;
        if (w <= x1 - x0 - 4) {
          ctx.fillStyle = COLORS.ink;
          ctx.fillText(label, (x0 + x1) / 2, pad.t - 8);
        } else if (ctx.measureText(s.surface).width <= x1 - x0 - 4) {
          ctx.fillStyle = COLORS.ink;
          ctx.fillText(s.surface, (x0 + x1) / 2, pad.t - 8);
        }
      }
      ctx.font = "11px system-ui";
    }

    drawContourPath(ctx, target.contour, tx, ty, COLORS.target, 2.2);
    if (showUserOverlay) {
      drawContourPath(ctx, result!.aligned_user as unknown as Pt[], tx, ty, COLORS.user, 2);
    }

    // playheads
    if (playheadTime != null && playheadTime >= 0 && playheadTime <= duration) {
      drawPlayhead(ctx, tx(playheadTime), pad.t, height - pad.b, "rgba(255,255,255,0.55)");
    }
    if (!separateUser && result && userPlayheadTime != null) {
      const mapped = warpToTarget(result.warp || [], userPlayheadTime);
      if (mapped !== null && mapped >= 0 && mapped <= duration) {
        drawPlayhead(ctx, tx(mapped), pad.t, height - pad.b, "rgba(217,89,38,0.7)");
      }
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
  }, [width, target, result, hover, playheadTime, userPlayheadTime, yMin, yMax, separateUser, spans, height]);

  // ---- separate user panel ----
  useEffect(() => {
    const canvas = userCanvasRef.current;
    if (!canvas || !separateUser || !result) return;
    const dpr = window.devicePixelRatio || 1;
    canvas.width = width * dpr;
    canvas.height = userHeight * dpr;
    canvas.style.width = `${width}px`;
    canvas.style.height = `${userHeight}px`;
    const ctx = canvas.getContext("2d")!;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, userHeight);

    const uPad = { l: pad.l, r: pad.r, t: 10, b: 24 };
    const uDur = Math.max(userDuration, 0.001);
    const utx = (t: number) => uPad.l + (t / uDur) * (width - uPad.l - uPad.r);
    const uty = (v: number) =>
      uPad.t + (1 - (v - yMin) / (yMax - yMin)) * (userHeight - uPad.t - uPad.b);

    ctx.strokeStyle = COLORS.grid;
    ctx.lineWidth = 1;
    ctx.fillStyle = COLORS.ink;
    ctx.font = "11px system-ui";
    ctx.textAlign = "right";
    const stStep = yMax - yMin > 18 ? 6 : 3;
    for (let v = Math.ceil(yMin / stStep) * stStep; v <= yMax; v += stStep) {
      const y = uty(v);
      ctx.beginPath();
      ctx.moveTo(uPad.l, y);
      ctx.lineTo(width - uPad.r, y);
      ctx.stroke();
      ctx.fillText(`${v > 0 ? "+" : ""}${v}`, uPad.l - 7, y + 3.5);
    }
    ctx.textAlign = "center";
    const tickStep = uDur > 6 ? 1 : 0.5;
    for (let t = 0; t <= uDur + 1e-6; t += tickStep) {
      ctx.fillText(`${t.toFixed(1)}s`, utx(t), userHeight - 8);
    }

    drawContourPath(ctx, result.user_contour as unknown as Pt[], utx, uty, COLORS.user, 2);

    if (userPlayheadTime != null && userPlayheadTime >= 0 && userPlayheadTime <= uDur) {
      drawPlayhead(ctx, utx(userPlayheadTime), uPad.t, userHeight - uPad.b, "rgba(217,89,38,0.7)");
    }
  }, [width, result, separateUser, userPlayheadTime, yMin, yMax, userDuration]);

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
  const hoverUser = hover && userLookup && showUserOverlay ? userLookup(hover.t) : null;
  const hoverWord =
    hover && spans ? spans.find((s) => hover.t >= s.start && hover.t <= s.end) : null;

  return (
    <div className="contour-wrap" ref={wrapRef}>
      <div className="diagram-legend">
        <span className="key">
          <span className="swatch" style={{ background: COLORS.target }} /> native audio
        </span>
        {result && (
          <span className="key">
            <span className="swatch" style={{ background: COLORS.user }} />
            {separateUser ? "you (own timeline, below)" : "you (time-aligned)"}
          </span>
        )}
        {result && result.divergences.length > 0 && (
          <span className="key">
            <span className="swatch" style={{ background: "rgba(250,178,25,0.6)" }} /> divergence
          </span>
        )}
        {spans && spans.length > 0 && (
          <span className="key hint" style={{ marginLeft: "auto" }}>
            word positions estimated
          </span>
        )}
      </div>
      <canvas
        ref={canvasRef}
        onMouseMove={onMove}
        onMouseLeave={() => setHover(null)}
        style={{ display: "block", width: "100%" }}
      />
      {separateUser && result && (
        <canvas ref={userCanvasRef} style={{ display: "block", width: "100%", marginTop: 4 }} />
      )}
      {hover && (hoverTarget !== null || hoverUser !== null || hoverWord) && (
        <div className="contour-tooltip" style={{ left: hover.px, top: 30 + wordBand }}>
          {hover.t.toFixed(2)}s
          {hoverWord && (
            <>
              {" · "}
              <span className="jp">{hoverWord.surface}</span>
            </>
          )}
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
