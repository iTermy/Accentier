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

export interface AccentData {
  moras: string[];
  accent: number | null;
  accent_source: string | null;
  pattern?: number[];
  category?: string;
  sentence_words?: WordAccent[];
}

export interface ItemSummary {
  id: number;
  expression: string;
  reading: string;
  sentence: string;
  sentence_audio: string;
  word_audio: string;
  accent: AccentData | null;
  due_at: number | null;
  interval_days: number | null;
  reps: number | null;
  last_score: number | null;
  attempt_count: number;
  best_score: number | null;
}

export interface TargetAnalysis {
  contour: [number, number | null][];
  ref_hz: number;
  duration: number;
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
  accent: AccentData | null;
  targets: { sentence?: TargetAnalysis; word?: TargetAnalysis };
  srs: any;
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
    no_voice?: boolean;
  };
  divergences: { start: number; end: number; kind: string; mean_dev_st: number }[];
  aligned_user: [number, number][];
  user_contour: [number, number][];
  notes: string[];
  target_ref_hz: number;
  user_ref_hz: number;
}
