// Free-study session: walk through a deck's (filtered, ordered) items one by
// one. The list is prepared by DeckPage and handed over via sessionStorage,
// so a page refresh resumes where you left off.
import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import PracticeCore from "../components/PracticeCore";

interface StudySession {
  ids: number[];
  deckId: string;
  deckName: string;
  pos: number;
}

const KEY = "accentier_study";

function loadSession(): StudySession | null {
  try {
    const raw = sessionStorage.getItem(KEY);
    if (!raw) return null;
    const s = JSON.parse(raw);
    if (!Array.isArray(s.ids) || !s.ids.length) return null;
    return { ...s, pos: Math.min(Math.max(s.pos || 0, 0), s.ids.length - 1) };
  } catch {
    return null;
  }
}

export default function StudyPage() {
  const [session, setSession] = useState<StudySession | null>(loadSession);
  const [done, setDone] = useState(false);
  const navigate = useNavigate();

  useEffect(() => {
    if (session) sessionStorage.setItem(KEY, JSON.stringify(session));
  }, [session]);

  if (!session)
    return (
      <div className="panel" style={{ textAlign: "center", padding: 48 }}>
        <h2>No study session</h2>
        <p className="hint">
          Open a <Link to="/">deck</Link>, filter/sort the list however you like, and hit{" "}
          <i>Start studying</i>.
        </p>
      </div>
    );

  if (done)
    return (
      <div className="panel" style={{ textAlign: "center", padding: 48 }}>
        <h2>Session complete 🎉</h2>
        <p className="hint">
          {session.ids.length} item{session.ids.length > 1 ? "s" : ""} from {session.deckName}.
        </p>
        <div style={{ display: "flex", gap: 10, justifyContent: "center", marginTop: 12 }}>
          <button
            onClick={() => {
              setSession({ ...session, pos: 0 });
              setDone(false);
            }}
          >
            Go again
          </button>
          <button className="primary" onClick={() => navigate(`/deck/${session.deckId}`)}>
            Back to deck
          </button>
        </div>
      </div>
    );

  const { ids, pos } = session;
  const goto = (p: number) => {
    if (p >= ids.length) setDone(true);
    else setSession({ ...session, pos: Math.max(0, p) });
  };

  return (
    <div>
      <div className="review-banner">
        <span>
          Studying <b>{session.deckName}</b> — {pos + 1} / {ids.length}
        </span>
        <div style={{ display: "flex", gap: 8 }}>
          <button className="small ghost" disabled={pos === 0} onClick={() => goto(pos - 1)}>
            ← Prev
          </button>
          <button className="small primary" onClick={() => goto(pos + 1)}>
            {pos + 1 >= ids.length ? "Finish" : "Next →"}
          </button>
        </div>
      </div>
      <PracticeCore key={ids[pos]} itemId={ids[pos]} />
    </div>
  );
}
