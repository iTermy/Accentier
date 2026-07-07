import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { ItemSummary, api } from "../api";

type Filter = "all" | "unpracticed" | "practiced" | "due";

export default function DeckPage() {
  const { deckId } = useParams();
  const [data, setData] = useState<{ deck: any; items: ItemSummary[] } | null>(null);
  const [filter, setFilter] = useState<Filter>("all");
  const [search, setSearch] = useState("");
  const navigate = useNavigate();

  useEffect(() => {
    api(`/api/decks/${deckId}/items`).then(setData).catch(() => {});
  }, [deckId]);

  const items = useMemo(() => {
    if (!data) return [];
    const now = Date.now() / 1000;
    let list = data.items;
    if (filter === "unpracticed") list = list.filter((i) => !i.attempt_count);
    if (filter === "practiced") list = list.filter((i) => i.attempt_count > 0);
    if (filter === "due") list = list.filter((i) => i.due_at !== null && i.due_at <= now);
    if (search.trim()) {
      const q = search.trim().toLowerCase();
      list = list.filter(
        (i) =>
          i.expression.toLowerCase().includes(q) ||
          (i.reading || "").toLowerCase().includes(q) ||
          (i.sentence || "").toLowerCase().includes(q)
      );
    }
    return list;
  }, [data, filter, search]);

  if (!data) return <span className="spin" />;
  const now = Date.now() / 1000;

  return (
    <div>
      <h2>{data.deck.name}</h2>
      <div style={{ display: "flex", gap: 10, margin: "12px 0", flexWrap: "wrap", alignItems: "center" }}>
        <div className="mode-toggle">
          {(["all", "unpracticed", "practiced", "due"] as Filter[]).map((f) => (
            <button key={f} className={filter === f ? "on" : ""} onClick={() => setFilter(f)}>
              {f}
            </button>
          ))}
        </div>
        <input
          type="text"
          placeholder="Search…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          style={{ maxWidth: 220 }}
        />
        <span className="hint">{items.length} items</span>
      </div>
      <div className="panel" style={{ padding: 0, overflowX: "auto" }}>
        <table className="items">
          <thead>
            <tr>
              <th>Expression</th>
              <th>Reading</th>
              <th>Accent</th>
              <th>Sentence</th>
              <th>Best</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {items.map((i) => (
              <tr key={i.id} className="clickable" onClick={() => navigate(`/practice/${i.id}`)}>
                <td className="jp" style={{ fontSize: 16, fontWeight: 600 }}>{i.expression}</td>
                <td className="jp" style={{ color: "var(--ink-2)" }}>{i.reading}</td>
                <td>
                  {i.accent?.accent !== null && i.accent?.accent !== undefined ? (
                    <span className="chip accent">[{i.accent.accent}]</span>
                  ) : (
                    <span className="hint">—</span>
                  )}
                </td>
                <td className="jp" style={{ maxWidth: 380, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", color: "var(--ink-2)" }}>
                  {i.sentence}
                </td>
                <td>
                  {i.best_score !== null ? (
                    <span className={`chip${i.best_score >= 85 ? " score-good" : ""}`}>{Math.round(i.best_score)}</span>
                  ) : (
                    <span className="hint">—</span>
                  )}
                </td>
                <td>
                  {i.due_at !== null && i.due_at <= now ? (
                    <span className="chip due">due</span>
                  ) : i.attempt_count > 0 ? (
                    <span className="hint">{i.interval_days ? `${Math.round(i.interval_days)}d` : "learning"}</span>
                  ) : (
                    <span className="hint">new</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
