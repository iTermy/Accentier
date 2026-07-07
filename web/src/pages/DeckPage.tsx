import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { ItemSummary, api } from "../api";

type Filter = "all" | "unpracticed" | "practiced" | "due";
type AccentFilter = "any" | "heiban" | "atamadaka" | "nakadaka" | "odaka" | "unknown";
type SortKey = "order" | "length" | "accent" | "best" | "due" | "attempts";
type StudyOrder = "start" | "end" | "shuffle";

const SORT_LABELS: Record<SortKey, string> = {
  order: "deck order",
  length: "length",
  accent: "accent number",
  best: "best score",
  due: "due soonest",
  attempts: "most practiced",
};

function categoryOf(i: ItemSummary): AccentFilter {
  const a = i.accent;
  if (!a || a.accent === null || a.accent === undefined) return "unknown";
  if (a.category === "heiban" || a.category === "atamadaka" || a.category === "nakadaka" || a.category === "odaka")
    return a.category;
  // derive when category missing
  const n = a.moras?.length ?? 0;
  if (a.accent === 0) return "heiban";
  if (a.accent === 1) return "atamadaka";
  if (n && a.accent >= n) return "odaka";
  return "nakadaka";
}

function lengthOf(i: ItemSummary): number {
  return i.accent?.moras?.length || i.reading?.length || i.expression.length;
}

export default function DeckPage() {
  const { deckId } = useParams();
  const [data, setData] = useState<{ deck: any; items: ItemSummary[] } | null>(null);
  const [filter, setFilter] = useState<Filter>("all");
  const [accentFilter, setAccentFilter] = useState<AccentFilter>("any");
  const [sortKey, setSortKey] = useState<SortKey>("order");
  const [sortDesc, setSortDesc] = useState(false);
  const [search, setSearch] = useState("");
  const [studyOrder, setStudyOrder] = useState<StudyOrder>("start");
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
    if (accentFilter !== "any") list = list.filter((i) => categoryOf(i) === accentFilter);
    if (search.trim()) {
      const q = search.trim().toLowerCase();
      list = list.filter(
        (i) =>
          i.expression.toLowerCase().includes(q) ||
          (i.reading || "").toLowerCase().includes(q) ||
          (i.sentence || "").toLowerCase().includes(q)
      );
    }
    if (sortKey !== "order") {
      const big = Number.POSITIVE_INFINITY;
      const val = (i: ItemSummary): number => {
        switch (sortKey) {
          case "length":
            return lengthOf(i);
          case "accent":
            return i.accent?.accent ?? big;
          case "best":
            return i.best_score ?? -1;
          case "due":
            return i.due_at ?? big;
          case "attempts":
            return -(i.attempt_count || 0); // "most practiced" ascending = most first
          default:
            return 0;
        }
      };
      list = [...list].sort((a, b) => val(a) - val(b) || a.id - b.id);
    }
    if (sortDesc) list = [...list].reverse();
    return list;
  }, [data, filter, accentFilter, search, sortKey, sortDesc]);

  const startStudy = () => {
    let ids = items.map((i) => i.id);
    if (!ids.length) return;
    if (studyOrder === "end") ids = [...ids].reverse();
    if (studyOrder === "shuffle") {
      ids = [...ids];
      for (let i = ids.length - 1; i > 0; i--) {
        const j = Math.floor(Math.random() * (i + 1));
        [ids[i], ids[j]] = [ids[j], ids[i]];
      }
    }
    sessionStorage.setItem(
      "accentier_study",
      JSON.stringify({ ids, deckId, deckName: data?.deck?.name || "", pos: 0 })
    );
    navigate("/study");
  };

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
        <select value={accentFilter} onChange={(e) => setAccentFilter(e.target.value as AccentFilter)}>
          <option value="any">any accent</option>
          <option value="heiban">heiban [0]</option>
          <option value="atamadaka">atamadaka [1]</option>
          <option value="nakadaka">nakadaka</option>
          <option value="odaka">odaka</option>
          <option value="unknown">no accent data</option>
        </select>
        <select value={sortKey} onChange={(e) => setSortKey(e.target.value as SortKey)}>
          {(Object.keys(SORT_LABELS) as SortKey[]).map((k) => (
            <option key={k} value={k}>
              sort: {SORT_LABELS[k]}
            </option>
          ))}
        </select>
        <button
          className="ghost small"
          title="Reverse order"
          onClick={() => setSortDesc(!sortDesc)}
          style={{ padding: "6px 10px" }}
        >
          {sortDesc ? "↓" : "↑"}
        </button>
        <input
          type="text"
          placeholder="Search…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          style={{ maxWidth: 220 }}
        />
        <span className="hint">{items.length} items</span>
      </div>

      <div
        className="panel"
        style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap", padding: "12px 16px", marginBottom: 12 }}
      >
        <b>Study these {items.length} items</b>
        <select value={studyOrder} onChange={(e) => setStudyOrder(e.target.value as StudyOrder)}>
          <option value="start">from the start</option>
          <option value="end">from the end</option>
          <option value="shuffle">shuffled</option>
        </select>
        <button className="primary" onClick={startStudy} disabled={!items.length}>
          Start studying
        </button>
        <span className="hint">Goes through every item in the list above, one by one.</span>
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
                    <span
                      className="chip accent"
                      title={i.accent.accent_source === "audio" ? "estimated from audio" : undefined}
                    >
                      [{i.accent.accent}]{i.accent.accent_source === "audio" ? "~" : ""}
                    </span>
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
