import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Network } from "lucide-react";
import { api } from "../lib/api";
import { errorText } from "../components/ui";

export default function Login({ configured }: { configured: boolean }) {
  const [pw, setPw] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const qc = useQueryClient();

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setErr("");
    try {
      await api.post("/api/auth/login", { password: pw });
      qc.removeQueries({ predicate: (q) => q.queryKey[0] !== "auth" });
      qc.setQueryData(["auth"], { authenticated: true, password_configured: true });
    } catch (ex) {
      setErr(errorText(ex));
      setPw("");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="login">
      <form className="card stack" onSubmit={submit}>
        <div className="row"><div className="brand-mark"><Network size={15} /></div><b>Knowledge OS</b></div>
        <div>
          <h1 className="title" style={{ fontSize: 20 }}>Welcome back</h1>
          <div className="muted small">Your private vault, graph and memory.</div>
        </div>
        {!configured ? (
          <div className="small">
            No password is set yet. On the server run:
            <pre className="card card-pad" style={{ margin: "8px 0 0", fontSize: 12, whiteSpace: "pre-wrap" }}>docker compose exec knowledge-os python -m knowledge_os set-password</pre>
          </div>
        ) : (
          <>
            <label className="small">Password
              <input className="input" type="password" autoComplete="current-password" autoFocus value={pw} onChange={(e) => setPw(e.target.value)} style={{ marginTop: 6 }} />
            </label>
            {err && <div className="small" style={{ color: "var(--bad)" }}>{err}</div>}
            <button className="btn primary" disabled={!pw || busy}>{busy ? "Checking…" : "Sign in"}</button>
          </>
        )}
      </form>
    </div>
  );
}
