/**
 * The authentication gate.
 *
 * Nothing behind the gate renders until `/auth/me` has answered, so a stale token
 * is caught here rather than by a panel failing after it has already drawn.
 */

import { useState } from "react";
import { ShieldCheck } from "lucide-react";

import { Button } from "@/components/ui/button";
import { ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";

function BrandMark() {
  return (
    <svg className="brand-mark" viewBox="0 0 38 42" aria-hidden="true">
      <path d="m19 2 16 9v20l-16 9L3 31V11zM10 13l9 5 9-5M10 13v14l9 5 9-5V13M19 18v14M3 11l7 2M35 11l-7 2M19 2v9" />
    </svg>
  );
}

export function LoginScreen() {
  const { login } = useAuth();
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setError("");
    setBusy(true);
    try {
      await login(username.trim(), password);
    } catch (err) {
      setError(
        err instanceof ApiError && err.status === 401
          ? "Invalid credentials."
          : err instanceof Error
            ? err.message
            : "Login failed.",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="ultron-shell login-shell">
      <form className="login-card" onSubmit={submit}>
        <div className="login-brand">
          <BrandMark />
          <div className="brand">
            <span>ULTRON</span>
            <p>CORE COMMAND CENTER</p>
          </div>
        </div>
        <p className="login-lede">Authenticate to open a session.</p>
        <label className="login-field">
          <span>OPERATOR</span>
          <input
            type="text"
            autoComplete="username"
            value={username}
            onChange={(event) => setUsername(event.target.value)}
            disabled={busy}
          />
        </label>
        <label className="login-field">
          <span>PASSPHRASE</span>
          <input
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            autoFocus
            disabled={busy}
          />
        </label>
        {error ? (
          <div className="login-error" role="alert">
            {error}
          </div>
        ) : null}
        <Button type="submit" className="login-submit" disabled={busy || !password}>
          <ShieldCheck />
          {busy ? "AUTHENTICATING…" : "AUTHENTICATE"}
        </Button>
      </form>
    </main>
  );
}
