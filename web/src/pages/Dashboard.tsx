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
  const [uploading, setUploading] = useState(false);
  const [uploadMsg, setUploadMsg] = useState("");
  const [drag, setDrag] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const navigate = useNavigate();

  const load = () => {
    api<Deck[]>("/api/decks").then(setDecks).catch(() => setDecks([]));
    api<Stats>("/api/stats").then(setStats).catch(() => {});
  };
  useEffect(load, []);

  const upload = async (file: File) => {
    if (!file.name.toLowerCase().endsWith(".apkg")) {
      setUploadMsg("Please choose an .apkg file exported from Anki.");
      return;
    }
    setUploading(true);
    setUploadMsg(`Importing ${file.name} — parsing notes and extracting audio…`);
    try {
      const form = new FormData();
      form.append("file", file);
      form.append("language", "auto");
      const res = await api<any>("/api/decks/upload", { method: "POST", body: form });
      setUploadMsg(
        `Imported ${res.items_imported} items (${res.language === "ja" ? "Japanese" : "generic"} mode, ${res.media_extracted} audio files).`
      );
      load();
    } catch (e: any) {
      setUploadMsg(`Import failed: ${e.message}`);
    } finally {
      setUploading(false);
    }
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
        <h2>Your decks</h2>
        {decks === null ? (
          <span className="spin" />
        ) : decks.length === 0 ? (
          <p className="hint">No decks yet — upload your Anki mining deck below to get started.</p>
        ) : (
          <div className="grid" style={{ gap: 10, marginTop: 8 }}>
            {decks.map((d) => (
              <div className="panel deck-card" key={d.id} style={{ background: "var(--panel-2)" }}>
                <div>
                  <b>{d.name}</b>{" "}
                  <span className="chip">{d.language === "ja" ? "Japanese · pitch accent" : "intonation"}</span>
                  <div className="meta">
                    {d.item_count} items · {d.practiced_count} practiced
                  </div>
                </div>
                <div style={{ display: "flex", gap: 8 }}>
                  <button onClick={() => navigate(`/deck/${d.id}`)}>Open</button>
                  <button
                    className="ghost"
                    onClick={async () => {
                      if (confirm(`Delete deck "${d.name}" and all its progress?`)) {
                        await api(`/api/decks/${d.id}`, { method: "DELETE" });
                        load();
                      }
                    }}
                  >
                    Delete
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}

        <div
          className={`upload-zone${drag ? " drag" : ""}`}
          style={{ marginTop: 16 }}
          onClick={() => !uploading && fileRef.current?.click()}
          onDragOver={(e) => {
            e.preventDefault();
            setDrag(true);
          }}
          onDragLeave={() => setDrag(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDrag(false);
            const f = e.dataTransfer.files[0];
            if (f && !uploading) upload(f);
          }}
        >
          {uploading ? (
            <span>
              <span className="spin" /> {uploadMsg}
            </span>
          ) : (
            <>
              <b>Drop an .apkg here</b> or click to choose
              <div className="hint" style={{ marginTop: 4 }}>
                Export from Anki with “Include media” checked. Large decks take a minute.
              </div>
              {uploadMsg && <div className="hint" style={{ marginTop: 8, color: "var(--ink)" }}>{uploadMsg}</div>}
            </>
          )}
          <input
            ref={fileRef}
            type="file"
            accept=".apkg"
            hidden
            onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])}
          />
        </div>
      </div>
    </div>
  );
}
