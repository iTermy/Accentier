import { FormEvent, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, setSession } from "../api";

export default function AuthPage({ onAuth }: { onAuth: () => void }) {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const navigate = useNavigate();

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setError("");
    setBusy(true);
    try {
      const res = await api<{ token: string; username: string }>(`/api/auth/${mode}`, {
        method: "POST",
        body: JSON.stringify({ username, password }),
      });
      setSession(res.token, res.username);
      onAuth();
      navigate("/");
    } catch (err: any) {
      setError(err.message || "Something went wrong");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={{ maxWidth: 380, margin: "60px auto" }}>
      <div className="panel">
        <h2>{mode === "login" ? "Welcome back" : "Create your account"}</h2>
        <p className="hint">
          Shadow your Anki mining decks and get real feedback on your pitch accent.
        </p>
        <form onSubmit={submit}>
          <label>Username</label>
          <input type="text" value={username} onChange={(e) => setUsername(e.target.value)} autoFocus />
          <label>Password</label>
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} />
          {error && <div className="error">{error}</div>}
          <div style={{ marginTop: 18, display: "flex", gap: 10, alignItems: "center" }}>
            <button className="primary" disabled={busy || !username || !password}>
              {busy ? <span className="spin" /> : mode === "login" ? "Sign in" : "Register"}
            </button>
            <button
              type="button"
              className="ghost small"
              onClick={() => setMode(mode === "login" ? "register" : "login")}
            >
              {mode === "login" ? "New here? Register" : "Have an account? Sign in"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
