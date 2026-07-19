// Pitch contour comparison chart (canvas), video-editor style.
// The timeline is pixels-per-second based with a fixed default time scale:
// VIEW_SECONDS of audio fit the viewport, anything longer scrolls (longer
// sentence = longer scroll). Zoom with the +/− buttons or ctrl+wheel; "fit"
// squeezes the whole take into view on demand. Click seeks playback;
// drag selects a slice to drill (the parent owns selection + playback).
// Target = blue line on the target timeline; user attempt = orange line,
// either pre-warped onto the target timeline (overlay) or on its own panel
// below with its own timeline (separate). Divergence regions arrive as
// [start,end] bands on the target timeline. Estimated word/mora spans are
// labeled in a band at the top. Playheads track both native-audio and
// your-take playback; in overlay mode the user playhead is mapped through
// the DTW warp so it rides the target timeline.
import { useEffect, useMemo, useRef, useState } from "react";
import { AttemptResult, TargetAnalysis, WordSpan } from "../api";

const COLORS = {
  target: "#3987e5",
  user: "#d95926",
  band: "rgba(250, 178, 25, 0.13)",
  bandEdge: "rgba(250, 178, 25, 0.35)",
  select: "rgba(126, 195, 255, 0.10)",
  selectEdge: "rgba(126, 195, 255, 0.75)",
  grid: "#262c38",
  wordTick: "rgba(255,255,255,0.09)",
  ink: "#a8afbd",
  inkFaint: "#6b7280",
  panelBg: "#151a23",
};

const JP_FONT = "12px 'Yu Gothic UI', 'Hiragino Kaku Gothic ProN', Meiryo, sans-serif";
const PAD_L = 42;
const PAD_R = 14;
const VIEW_SECONDS = 4; // default time scale: this much audio per viewport width
const MIN_PPS = 8;

// grid spacing for a given semitone range
const stStepFor = (range: number) => (range > 18 ? 6 : range > 10 ? 3 : 2);

export interface Selection {
  start: number;
  end: number;
}

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

function niceTickStep(pps: number): number {
  // aim for ~90 px between time ticks
  const raw = 90 / pps;
  for (const s of [0.1, 0.2, 0.25, 0.5, 1, 2, 5, 10]) if (s >= raw) return s;
  return 20;
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
  selection = null,
  onSelectionChange,
  onSeek,
}: {
  target: TargetAnalysis;
  result: AttemptResult | null;
  playheadTime?: number | null;
  userPlayheadTime?: number | null;
  separateUser?: boolean;
  selection?: Selection | null;
  onSelectionChange?: (sel: Selection | null) => void;
  onSeek?: (t: number) => void;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const axisRef = useRef<HTMLCanvasElement>(null);
  const userCanvasRef = useRef<HTMLCanvasElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(800);
  const [scrollLeft, setScrollLeft] = useState(0);
  const [hover, setHover] = useState<{ px: number; t: number } | null>(null);
  const [pps, setPps] = useState<number | null>(null); // null = auto
  const dragRef = useRef<{ t0: number; px0: number; moved: boolean } | null>(null);

  const spans: WordSpan[] | undefined = target.words || target.moras;
  const wordBand = spans && spans.length > 0 ? 22 : 0;
  const height = 240 + wordBand;
  const userHeight = 150;
  const pad = { l: PAD_L, r: PAD_R, t: 14 + wordBand, b: 26 };

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

  const fitPps = Math.max(MIN_PPS, (width - pad.l - pad.r) / Math.max(duration, 0.001));
  // default: fixed time scale — VIEW_SECONDS per viewport, longer audio scrolls
  const viewPps = (width - pad.l - pad.r) / VIEW_SECONDS;
  // canvases cap out around 32k device px — don't let zoom blow past that
  const maxPps = Math.min(1200, 28000 / ((window.devicePixelRatio || 1) * Math.max(duration, 0.001)));
  const effPps = Math.min(maxPps, Math.max(MIN_PPS, pps ?? viewPps));
  const chartW = Math.max(width, Math.ceil(pad.l + duration * effPps + pad.r));
  const scrollable = chartW > width + 1;

  // reset zoom when the item/mode changes (duration is the cheapest key)
  useEffect(() => setPps(null), [duration]);

  const userDuration = useMemo(() => {
    if (!result) return 0;
    if (result.user_duration) return result.user_duration;
    const uc = result.user_contour;
    return uc.length ? uc[uc.length - 1][0] : 0;
  }, [result]);

  // shared y-range across both panels so the same drop looks the same size.
  // Percentile-based: a handful of outlier frames (expressive spikes, residual
  // octave errors) must not stretch the axis until accent drops turn flat.
  const { yMin, yMax } = useMemo(() => {
    const vals: number[] = [];
    for (const [, v] of target.contour) if (v !== null) vals.push(v);
    if (result) for (const [, v] of result.user_contour) if (v !== null) vals.push(v);
    if (!vals.length) return { yMin: -6, yMax: 6 };
    vals.sort((a, b) => a - b);
    const q = (p: number) => vals[Math.round(p * (vals.length - 1))];
    const lo = q(0.02), hi = q(0.98);
    const mid = (lo + hi) / 2;
    // at least ±4 st, so a near-flat take isn't magnified into fake hills
    const half = Math.max(4, (hi - lo) / 2 + Math.max(1, (hi - lo) * 0.12));
    return { yMin: mid - half, yMax: mid + half };
  }, [target, result]);

  const tx = (t: number) => pad.l + t * effPps;
  const pxToT = (px: number) => (px - pad.l) / effPps;
  const ty = (v: number) => {
    const c = Math.min(yMax, Math.max(yMin, v)); // outliers pin to the edge
    return pad.t + (1 - (c - yMin) / (yMax - yMin)) * (height - pad.t - pad.b);
  };
  const clampT = (t: number) => Math.min(duration, Math.max(0, t));

  // ---- main (target) panel ----
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const dpr = window.devicePixelRatio || 1;
    canvas.width = chartW * dpr;
    canvas.height = height * dpr;
    canvas.style.width = `${chartW}px`;
    canvas.style.height = `${height}px`;
    const ctx = canvas.getContext("2d")!;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, chartW, height);

    // selection region (under everything)
    if (selection) {
      const x0 = tx(selection.start), x1 = tx(selection.end);
      ctx.fillStyle = COLORS.select;
      ctx.fillRect(x0, pad.t, x1 - x0, height - pad.t - pad.b);
      ctx.strokeStyle = COLORS.selectEdge;
      ctx.lineWidth = 1.5;
      for (const x of [x0, x1]) {
        ctx.beginPath();
        ctx.moveTo(x, pad.t);
        ctx.lineTo(x, height - pad.b);
        ctx.stroke();
      }
    }

    // divergence bands
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

    // grid: semitone lines (labels live on the sticky axis overlay)
    ctx.strokeStyle = COLORS.grid;
    ctx.lineWidth = 1;
    ctx.fillStyle = COLORS.ink;
    ctx.font = "11px system-ui";
    const stStep = stStepFor(yMax - yMin);
    for (let v = Math.ceil(yMin / stStep) * stStep; v <= yMax; v += stStep) {
      const y = ty(v);
      ctx.beginPath();
      ctx.moveTo(pad.l, y);
      ctx.lineTo(chartW - pad.r, y);
      ctx.stroke();
    }
    // x ticks
    ctx.textAlign = "center";
    const tickStep = niceTickStep(effPps);
    for (let t = 0; t <= duration + 1e-6; t += tickStep) {
      ctx.fillText(`${t.toFixed(tickStep < 1 ? 1 : 0)}s`, tx(t), height - 8);
    }

    // estimated word/mora spans: boundary ticks + labels in the top band,
    // with alternating shading so each label visibly owns its stretch
    if (spans && spans.length) {
      spans.forEach((s, i) => {
        if (i % 2 === 0) return;
        ctx.fillStyle = "rgba(255,255,255,0.045)";
        ctx.fillRect(tx(s.start), pad.t - 22, tx(s.end) - tx(s.start), 20);
      });
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
  }, [chartW, effPps, target, result, hover, playheadTime, userPlayheadTime, yMin, yMax,
      separateUser, spans, height, selection]);

  // ---- sticky y-axis overlay (stays put while the chart scrolls) ----
  useEffect(() => {
    const canvas = axisRef.current;
    if (!canvas) return;
    const dpr = window.devicePixelRatio || 1;
    canvas.width = pad.l * dpr;
    canvas.height = height * dpr;
    canvas.style.width = `${pad.l}px`;
    canvas.style.height = `${height}px`;
    const ctx = canvas.getContext("2d")!;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, pad.l, height);
    if (scrollLeft > 0) {
      // hide the contour sliding underneath the labels
      ctx.fillStyle = COLORS.panelBg;
      ctx.fillRect(0, 0, pad.l - 1, height);
    }
    ctx.fillStyle = COLORS.ink;
    ctx.font = "11px system-ui";
    ctx.textAlign = "right";
    const stStep = stStepFor(yMax - yMin);
    for (let v = Math.ceil(yMin / stStep) * stStep; v <= yMax; v += stStep) {
      ctx.fillText(`${v > 0 ? "+" : ""}${v}`, pad.l - 7, ty(v) + 3.5);
    }
    ctx.save();
    ctx.translate(11, height / 2);
    ctx.rotate(-Math.PI / 2);
    ctx.textAlign = "center";
    ctx.fillText("semitones vs median", 0, 0);
    ctx.restore();
  }, [height, yMin, yMax, scrollLeft > 0, pad.t]);

  // ---- separate user panel (fit-width, its own timeline) ----
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
    const uty = (v: number) => {
      const c = Math.min(yMax, Math.max(yMin, v));
      return uPad.t + (1 - (c - yMin) / (yMax - yMin)) * (userHeight - uPad.t - uPad.b);
    };

    ctx.strokeStyle = COLORS.grid;
    ctx.lineWidth = 1;
    ctx.fillStyle = COLORS.ink;
    ctx.font = "11px system-ui";
    ctx.textAlign = "right";
    const stStep = stStepFor(yMax - yMin);
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

  // keep the playhead in view while audio plays
  useEffect(() => {
    const sc = scrollRef.current;
    if (!sc || !scrollable) return;
    let t: number | null = null;
    if (playheadTime != null) t = playheadTime;
    else if (!separateUser && result && userPlayheadTime != null)
      t = warpToTarget(result.warp || [], userPlayheadTime);
    if (t === null) return;
    const px = tx(t);
    if (px < sc.scrollLeft + pad.l + 4 || px > sc.scrollLeft + sc.clientWidth - 24) {
      sc.scrollLeft = Math.max(0, px - sc.clientWidth * 0.25);
    }
  }, [playheadTime, userPlayheadTime, scrollable, effPps]);

  // ctrl+wheel zoom anchored at the cursor (non-passive listener)
  const zoomStateRef = useRef({ effPps, fitPps, maxPps });
  zoomStateRef.current = { effPps, fitPps, maxPps };
  useEffect(() => {
    const sc = scrollRef.current;
    if (!sc) return;
    const onWheel = (e: WheelEvent) => {
      if (!e.ctrlKey) return;
      e.preventDefault();
      const { effPps: cur, maxPps: cap } = zoomStateRef.current;
      const factor = e.deltaY < 0 ? 1.25 : 0.8;
      const next = Math.min(cap, Math.max(MIN_PPS, cur * factor));
      if (next === cur) return;
      const rect = sc.getBoundingClientRect();
      const cursorPx = e.clientX - rect.left + sc.scrollLeft;
      const t = (cursorPx - PAD_L) / cur;
      setPps(next);
      requestAnimationFrame(() => {
        sc.scrollLeft = Math.max(0, PAD_L + t * next - (e.clientX - rect.left));
      });
    };
    sc.addEventListener("wheel", onWheel, { passive: false });
    return () => sc.removeEventListener("wheel", onWheel);
  }, []);

  const zoomBy = (factor: number) => {
    const sc = scrollRef.current;
    const center = sc ? sc.scrollLeft + sc.clientWidth / 2 : 0;
    const t = pxToT(center);
    const next = Math.min(maxPps, Math.max(MIN_PPS, effPps * factor));
    setPps(next);
    if (sc) requestAnimationFrame(() => {
      sc.scrollLeft = Math.max(0, PAD_L + t * next - sc.clientWidth / 2);
    });
  };

  const zoomToSelection = () => {
    if (!selection) return;
    const span = Math.max(0.15, selection.end - selection.start);
    const next = Math.min(maxPps, Math.max(MIN_PPS, (width - pad.l - pad.r) / span));
    setPps(next);
    const sc = scrollRef.current;
    if (sc) requestAnimationFrame(() => {
      sc.scrollLeft = Math.max(0, PAD_L + selection.start * next - pad.l);
    });
  };

  const eventT = (e: React.MouseEvent) => {
    const rect = canvasRef.current!.getBoundingClientRect();
    return { px: e.clientX - rect.left, t: clampT(pxToT(e.clientX - rect.left)) };
  };

  // drag tracking lives on window so a drag that leaves the canvas (or the
  // browser window) still finalizes instead of silently vanishing
  const onDown = (e: React.MouseEvent) => {
    if (e.button !== 0) return;
    const canvas = canvasRef.current!;
    const rect0 = canvas.getBoundingClientRect();
    const px0 = e.clientX - rect0.left;
    const drag = { t0: clampT(pxToT(px0)), px0, moved: false };
    dragRef.current = drag;
    const winMove = (ev: MouseEvent) => {
      const rect = canvas.getBoundingClientRect();
      const px = ev.clientX - rect.left;
      const t = clampT(pxToT(px));
      if (!drag.moved && Math.abs(px - drag.px0) > 5) drag.moved = true;
      if (drag.moved && onSelectionChange) {
        onSelectionChange({ start: Math.min(drag.t0, t), end: Math.max(drag.t0, t) });
      }
    };
    const winUp = (ev: MouseEvent) => {
      window.removeEventListener("mousemove", winMove);
      window.removeEventListener("mouseup", winUp);
      dragRef.current = null;
      const rect = canvas.getBoundingClientRect();
      const t = clampT(pxToT(ev.clientX - rect.left));
      if (!drag.moved) {
        onSeek?.(t);
      } else if (onSelectionChange) {
        const sel = { start: Math.min(drag.t0, t), end: Math.max(drag.t0, t) };
        // a sliver of a selection is a mis-click, not a slice
        onSelectionChange(sel.end - sel.start >= 0.1 ? sel : null);
      }
    };
    window.addEventListener("mousemove", winMove);
    window.addEventListener("mouseup", winUp);
  };

  const onMove = (e: React.MouseEvent) => {
    const { px, t } = eventT(e);
    if (px < pad.l - 2 || px > chartW - pad.r + 2) {
      setHover(null);
    } else {
      setHover({ px, t });
    }
  };

  const targetLookup = useMemo(
    () => buildLookup(target.contour.filter((p): p is [number, number] => p[1] !== null)),
    [target]
  );
  const userLookup = useMemo(
    () => (result ? buildLookup(result.aligned_user) : null),
    [result]
  );

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
        <span className="zoom-controls" style={{ marginLeft: "auto" }}>
          {selection && (
            <button className="ghost small" onClick={zoomToSelection} title="Zoom the chart to the selected slice">
              zoom to slice
            </button>
          )}
          <button className="ghost small" onClick={() => zoomBy(1 / 1.4)} title="Zoom out (or ctrl+scroll)">−</button>
          <button className="ghost small" onClick={() => zoomBy(1.4)} title="Zoom in (or ctrl+scroll)">+</button>
          <button
            className="ghost small"
            onClick={() => setPps(fitPps)}
            disabled={!scrollable}
            title="Fit the whole audio in view"
          >
            fit
          </button>
        </span>
      </div>
      <div style={{ position: "relative" }}>
        <div
          className="chart-scroll"
          ref={scrollRef}
          onScroll={(e) => setScrollLeft((e.target as HTMLDivElement).scrollLeft)}
        >
          <canvas
            ref={canvasRef}
            onMouseDown={onDown}
            onMouseMove={onMove}
            onMouseLeave={() => setHover(null)}
            style={{ display: "block", cursor: onSeek ? "crosshair" : "default" }}
          />
        </div>
        <canvas
          ref={axisRef}
          style={{ position: "absolute", left: 0, top: 0, pointerEvents: "none" }}
        />
        {hover && !dragRef.current && (hoverTarget !== null || hoverUser !== null || hoverWord) && (
          <div className="contour-tooltip" style={{ left: hover.px - scrollLeft, top: 16 + wordBand }}>
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
      <p className="hint" style={{ margin: "4px 0 0", fontSize: 12 }}>
        click the chart to play from there · drag to select a slice to drill
      </p>
      {separateUser && result && (
        <canvas ref={userCanvasRef} style={{ display: "block", width: "100%", marginTop: 4 }} />
      )}
    </div>
  );
}
