import { useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Deck, api } from "../api";

interface Stats {
  total_attempts: number;
  practiced_items: number;
  avg_recent_score: number | null;
  due_now: number;
  attempts_this_week: number;
}

export default function Dashboard() {
  const [decks, setDecks] = useState<Deck[] | null>(null);
  const [stats, setStats] = useState<Stats | null>(null);
  const [syncing, setSyncing] = useState(false);
  const [syncMsg, setSyncMsg] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);
  const navigate = useNavigate();

  const load = () => {
    api<Deck[]>("/api/decks").then(setDecks).catch(() => setDecks([]));
    api<Stats>("/api/stats").then(setStats).catch(() => {});
  };
  useEffect(load, []);

  const deck = decks?.[0] ?? null;

  const syncProgress = async (file: File) => {
    if (!file.name.toLowerCase().endsWith(".apkg")) {
      setSyncMsg("Please choose an .apkg file exported from Anki.");
      return;
    }
    setSyncing(true);
    setSyncMsg("Reading your Anki export…");
    try {
      const form = new FormData();
      form.append("file", file);
      const res = await api<{ studied_notes: number; known_count: number }>(
        "/api/progress/sync",
        { method: "POST", body: form }
      );
      setSyncMsg(
        `Matched ${res.known_count} studied words. Use the “studied” filter on the deck page to practice just those.`
      );
      load();
    } catch (e: any) {
      setSyncMsg(`Sync failed: ${e.message}`);
    } finally {
      setSyncing(false);
    }
  };

  const clearProgress = async () => {
    await api("/api/progress/sync", { method: "DELETE" });
    setSyncMsg("Anki progress cleared — all 1,500 words are shown again.");
    load();
  };

  return (
    <div className="grid" style={{ gap: 20 }}>
      {stats && (
        <div className="grid cols-4">
          <div className="panel stat">
            <div className="value" style={{ color: stats.due_now ? "var(--warn)" : undefined }}>
              {stats.due_now}
            </div>
            <div className="label">Due for review</div>
          </div>
          <div className="panel stat">
            <div className="value">{stats.practiced_items}</div>
            <div className="label">Items practiced</div>
          </div>
          <div className="panel stat">
            <div className="value">{stats.attempts_this_week}</div>
            <div className="label">Attempts this week</div>
          </div>
          <div className="panel stat">
            <div className="value">{stats.avg_recent_score ?? "—"}</div>
            <div className="label">Avg score (last 30)</div>
          </div>
        </div>
      )}

      {stats && stats.due_now > 0 && (
        <div className="review-banner">
          <span>
            <b>{stats.due_now}</b> item{stats.due_now === 1 ? "" : "s"} due for shadowing review.
          </span>
          <Link to="/review">
            <button className="primary small">Start review</button>
          </Link>
        </div>
      )}

      <div className="panel">
        {decks === null ? (
          <span className="spin" />
        ) : deck ? (
          <div className="deck-card" style={{ display: "flex", alignItems: "center", gap: 16, flexWrap: "wrap" }}>
            <div style={{ flex: 1, minWidth: 220 }}>
              <h2 style={{ margin: 0 }}>{deck.name}</h2>
              <div className="meta" style={{ marginTop: 6 }}>
                {deck.item_count.toLocaleString()} words with native audio, curated pitch accents and
                sentence diagrams · {deck.practiced_count} practiced
                {deck.known_count > 0 && <> · filtered set: {deck.known_count} studied in your Anki</>}
              </div>
              <p className="hint" style={{ marginTop: 8, marginBottom: 0 }}>
                Shadow each word and its sentence, and get scored on how closely your pitch tracks the
                native speaker's.
              </p>
            </div>
            <button className="success" onClick={() => navigate(`/deck/${deck.id}`)}>
              Open deck
            </button>
          </div>
        ) : (
          <p className="hint">
            The built-in Kaishi 1.5k deck hasn't been imported yet — restart the server and check its
            logs (it seeds itself from <code>resources/Kaishi 1.5k.apkg</code> on startup).
          </p>
        )}
      </div>

      {deck && (
        <div className="panel">
          <h2>Already partway through Kaishi in Anki?</h2>
          <p className="hint" style={{ marginTop: 6 }}>
            Upload your own Kaishi 1.5k export and Accentier will mark the words you've already studied,
            so you can shadow just those instead of all 1,500. In Anki: deck options → Export →
            file type <b>.apkg</b>, with <b>“Include scheduling information”</b> checked (media is not
            needed).
          </p>
          <div style={{ display: "flex", gap: 10, alignItems: "center", marginTop: 10, flexWrap: "wrap" }}>
            <button onClick={() => !syncing && fileRef.current?.click()} disabled={syncing}>
              {syncing ? "Syncing…" : deck.known_count > 0 ? "Re-sync Anki progress" : "Sync Anki progress"}
            </button>
            {deck.known_count > 0 && (
              <button className="ghost" onClick={clearProgress} disabled={syncing}>
                Clear ({deck.known_count} words)
              </button>
            )}
            {syncMsg && <span className="hint" style={{ color: "var(--ink)" }}>{syncMsg}</span>}
          </div>
          <input
            ref={fileRef}
            type="file"
            accept=".apkg"
            hidden
            onChange={(e) => {
              if (e.target.files?.[0]) syncProgress(e.target.files[0]);
              e.target.value = "";
            }}
          />
        </div>
      )}
    </div>
  );
}
