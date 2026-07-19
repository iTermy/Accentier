import { useEffect, useState } from "react";
import {
  HashRouter,
  Link,
  NavLink,
  Navigate,
  Route,
  Routes,
  useNavigate,
} from "react-router-dom";
import { clearSession, getUser } from "./api";
import AuthPage from "./pages/AuthPage";
import Dashboard from "./pages/Dashboard";
import DeckPage from "./pages/DeckPage";
import PracticePage from "./pages/PracticePage";
import ReviewPage from "./pages/ReviewPage";
import StudyPage from "./pages/StudyPage";

function TopBar({ onLogout }: { onLogout: () => void }) {
  const user = getUser();
  const navigate = useNavigate();
  return (
    <header className="topbar">
      <Link to="/" className="logo">
        Accent<span>ier</span>
      </Link>
      {user && (
        <>
          <nav>
            <NavLink to="/" end>
              Home
            </NavLink>
            <NavLink to="/review">Review</NavLink>
          </nav>
          <span className="user">{user}</span>
          <button
            className="small ghost"
            onClick={() => {
              clearSession();
              onLogout();
              navigate("/login");
            }}
          >
            Sign out
          </button>
        </>
      )}
    </header>
  );
}

export default function App() {
  const [, force] = useState(0);
  const rerender = () => force((n) => n + 1);
  const authed = !!getUser();

  useEffect(() => {
    const onHash = () => rerender();
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  return (
    <HashRouter>
      <div className="shell">
        <TopBar onLogout={rerender} />
        <Routes>
          <Route path="/login" element={<AuthPage onAuth={rerender} />} />
          <Route path="/" element={authed ? <Dashboard /> : <Navigate to="/login" />} />
          <Route path="/deck/:deckId" element={authed ? <DeckPage /> : <Navigate to="/login" />} />
          <Route path="/practice/:itemId" element={authed ? <PracticePage /> : <Navigate to="/login" />} />
          <Route path="/review" element={authed ? <ReviewPage /> : <Navigate to="/login" />} />
          <Route path="/study" element={authed ? <StudyPage /> : <Navigate to="/login" />} />
          <Route path="*" element={<Navigate to="/" />} />
        </Routes>
      </div>
    </HashRouter>
  );
}
