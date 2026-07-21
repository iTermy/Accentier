// Thin API client. Token lives in localStorage; media URLs carry it as a
// query param because <audio src> can't set headers.

let token: string | null = localStorage.getItem("accentier_token");
let username: string | null = localStorage.getItem("accentier_user");

export function getUser(): string | null {
  return token ? username : null;
}

export function setSession(t: string, u: string) {
  token = t;
  username = u;
  localStorage.setItem("accentier_token", t);
  localStorage.setItem("accentier_user", u);
}

export function clearSession() {
  token = null;
  username = null;
  localStorage.removeItem("accentier_token");
  localStorage.removeItem("accentier_user");
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export async function api<T = any>(path: string, opts: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = { ...(opts.headers as any) };
  if (token) headers["Authorization"] = `Bearer ${token}`;
  if (opts.body && typeof opts.body === "string") headers["Content-Type"] = "application/json";
  const res = await fetch(path, { ...opts, headers });
  if (res.status === 401 && !path.startsWith("/api/auth/")) {
    clearSession();
    window.location.hash = "#/login";
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const j = await res.json();
      detail = j.detail || detail;
    } catch {}
    throw new ApiError(res.status, detail);
  }
  return res.json();
}

export function mediaUrl(deckId: number, filename: string): string {
  return `/api/media/${deckId}/${encodeURIComponent(filename)}?token=${encodeURIComponent(token || "")}`;
}

// ---- shared types ----

export interface Deck {
  id: number;
  name: string;
  language: string;
  item_count: number;
  practiced_count: number;
  known_count: number;
  is_builtin: number;
  created_at: number;
}

export interface WordAccent {
  surface: string;
  kana?: string;
  moras: string[];
  accent: number | null;
  pattern?: number[];
  pos?: string;
  content?: boolean;
}

export interface AccentPhrase {
  surface: string;
  moras: string[];
  accent: number;
  pattern: number[];
  level: number; // downstep level: 0 = phrase-reset height, higher = lower plateau
  words: { surface: string; at: number }[];
  break_after: boolean;
}

export interface AccentData {
  moras: string[];
  accent: number | null;
  accent_source: string | null;
  alternates?: number[];
  pattern?: number[];
  category?: string;
  sentence_words?: WordAccent[];
  sentence_phrases?: AccentPhrase[];
  sentence_hints?: string[];
}

export interface ItemSummary {
  id: number;
  expression: string;
  reading: string;
  sentence: string;
  sentence_audio: string;
  word_audio: string;
  word_meaning?: string;
  pitch_notes?: string;
  known: boolean;
  accent: AccentData | null;
  due_at: number | null;
  interval_days: number | null;
  reps: number | null;
  last_score: number | null;
  attempt_count: number;
  best_score: number | null;
}

export interface WordSpan {
  surface: string;
  start: number;
  end: number;
  accent?: number | null;
  moras?: string[];
}

export interface TargetAnalysis {
  contour: [number, number | null][];
  ref_hz: number;
  duration: number;
  words?: WordSpan[]; // estimated word spans (sentence mode)
  moras?: WordSpan[]; // estimated mora spans (word mode)
}

export interface ItemDetail {
  id: number;
  deck_id: number;
  deck_name: string;
  language: string;
  expression: string;
  reading: string;
  sentence: string;
  sentence_audio: string;
  word_audio: string;
  word_meaning?: string | null;
  sentence_meaning?: string | null;
  pitch_notes?: string | null;
  accent: AccentData | null;
  targets: { sentence?: TargetAnalysis; word?: TargetAnalysis };
  srs: Record<string, any>;
}

export interface AttemptResult {
  score: number;
  metrics: {
    shape: number;
    direction: number;
    level: number;
    timing: number;
    duration_ratio: number;
    mean_abs_dev_st?: number;
    warp?: number; // DTW path diagonality 0..1 — low = heavy time-warping
    no_voice?: boolean;
  };
  divergences: { start: number; end: number; kind: string; mean_dev_st: number }[];
  aligned_user: [number, number][];
  user_contour: [number, number][];
  warp: [number, number][]; // sparse [user_time, target_time] pairs from DTW
  notes: string[];
  target_ref_hz: number;
  user_ref_hz: number;
  user_duration?: number;
  slice?: [number, number]; // present when scored against a target sub-region
}
