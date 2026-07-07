import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import PracticeCore from "../components/PracticeCore";

interface QueueItem {
  id: number;
  expression: string;
  reading: string;
  deck_name: string;
  last_score: number | null;
}

export default function ReviewPage() {
  const [queue, setQueue] = useState<QueueItem[] | null>(null);
  const [pos, setPos] = useState(0);
  const [started, setStarted] = useState(false);
  const [scored, setScored] = useState(false);

  useEffect(() => {
    api<QueueItem[]>("/api/review/queue").then(setQueue).catch(() => setQueue([]));
  }, []);

  if (queue === null) return <span className="spin" />;

  if (!queue.length && !started)
    return (
      <div className="panel" style={{ textAlign: "center", padding: 48 }}>
        <h2>All caught up</h2>
        <p className="hint">
          Nothing due right now. Practice new items from your <Link to="/">decks</Link> and they'll come
          back here on a spaced schedule.
        </p>
      </div>
    );

  if (!started)
    return (
      <div className="panel">
        <h2>Review queue</h2>
        <p className="hint">
          {queue.length} item{queue.length > 1 ? "s" : ""} due. You'll shadow each one; the score decides
          when it comes back.
        </p>
        <table className="items" style={{ margin: "12px 0" }}>
          <tbody>
            {queue.slice(0, 12).map((q) => (
              <tr key={q.id}>
                <td className="jp" style={{ fontWeight: 600 }}>{q.expression}</td>
                <td className="jp" style={{ color: "var(--ink-2)" }}>{q.reading}</td>
                <td className="hint">{q.deck_name}</td>
                <td>{q.last_score !== null && <span className="chip">last {Math.round(q.last_score)}</span>}</td>
              </tr>
            ))}
            {queue.length > 12 && (
              <tr>
                <td colSpan={4} className="hint">
                  … and {queue.length - 12} more
                </td>
              </tr>
            )}
          </tbody>
        </table>
        <button className="primary" onClick={() => setStarted(true)}>
          Start review
        </button>
      </div>
    );

  if (pos >= queue.length)
    return (
      <div className="panel" style={{ textAlign: "center", padding: 48 }}>
        <h2>Review complete 🎉</h2>
        <p className="hint">{queue.length} items shadowed. Come back when more are due.</p>
        <Link to="/">
          <button style={{ marginTop: 8 }}>Back to decks</button>
        </Link>
      </div>
    );

  const current = queue[pos];
  return (
    <div>
      <div className="review-banner">
        <span>
          Review {pos + 1} / {queue.length}
        </span>
        <div style={{ display: "flex", gap: 8 }}>
          <button
            className="small ghost"
            onClick={() => {
              setPos(pos + 1);
              setScored(false);
            }}
          >
            Skip
          </button>
          {scored && (
            <button
              className="small primary"
              onClick={() => {
                setPos(pos + 1);
                setScored(false);
              }}
            >
              Next →
            </button>
          )}
        </div>
      </div>
      <PracticeCore key={current.id} itemId={current.id} onScored={() => setScored(true)} />
    </div>
  );
}
