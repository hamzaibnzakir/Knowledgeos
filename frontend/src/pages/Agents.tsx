import { useState } from "react";
import { Link } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy, KeyRound, Pencil, ShieldOff } from "lucide-react";
import { api, qs } from "../lib/api";
import type { Credential } from "../lib/types";
import { Empty, errorText, Modal, timeAgo, useDialog, useToast } from "../components/ui";

interface McpInfo {
  endpoint: string;
  oauth: { issuer: string; metadata: string; scopes: string[] };
  static_write_token_configured: boolean;
  rate_limits: { writes_per_min: number; reads_per_min: number };
  tools: { name: string; description: string; scope: string; destructive: boolean }[];
  legacy_aliases: string[];
}

export default function AgentsPage() {
  const qc = useQueryClient();
  const toast = useToast();
  const { prompt, confirm } = useDialog();
  const info = useQuery({ queryKey: ["mcp"], queryFn: () => api.get<McpInfo>("/api/mcp") });
  const creds = useQuery({ queryKey: ["agents"], queryFn: () => api.get<Credential[]>("/api/agents") });
  const [issuing, setIssuing] = useState(false);
  const [label, setLabel] = useState("");
  const [write, setWrite] = useState(true);
  const [issued, setIssued] = useState<{ token: string; label: string } | null>(null);

  const copy = (v: string) => navigator.clipboard.writeText(v).then(() => toast("Copied"), () => toast("Copy failed", { kind: "error" }));

  const issue = async () => {
    try {
      const r = await api.post<{ token: string; label: string }>("/api/agents/tokens", { label, scopes: write ? ["knowledge:read", "knowledge:write"] : ["knowledge:read"] });
      setIssuing(false);
      setIssued(r);
      setLabel("");
      qc.invalidateQueries({ queryKey: ["agents"] });
    } catch (e) { toast(errorText(e), { kind: "error" }); }
  };
  const revoke = async (c: Credential) => {
    if (!(await confirm(`Revoke ${c.label}?`, { body: "The agent loses access immediately. It can reconnect only with new credentials.", danger: true, confirm: "Revoke" }))) return;
    await api.post(`/api/agents/${encodeURIComponent(c.client_id)}/revoke`);
    qc.invalidateQueries({ queryKey: ["agents"] });
  };
  const rename = async (c: Credential) => {
    const v = await prompt("Rename agent", { value: c.label });
    if (v) { await api.patch(`/api/agents/${encodeURIComponent(c.client_id)}`, { label: v }); qc.invalidateQueries({ queryKey: ["agents"] }); }
  };

  const config = info.data ? JSON.stringify({ mcpServers: { "knowledge-os": { type: "http", url: info.data.endpoint, headers: { Authorization: "Bearer <agent token>" } } } }, null, 2) : "";

  return (
    <div className="page">
      <div className="page-inner">
        <div className="spread">
          <div><h1 className="title">Agents &amp; MCP</h1><div className="muted">Every AI client that can reach your vault, what it may do, and what it did.</div></div>
          <button className="btn primary" onClick={() => setIssuing(true)}><KeyRound />New agent token</button>
        </div>

        <h2 className="section">Connect</h2>
        <div className="grid cols-2">
          <div className="card card-pad stack">
            <b>MCP endpoint</b>
            <div className="row"><code className="grow" style={{ wordBreak: "break-all" }}>{info.data?.endpoint}</code><button className="btn sm icon" onClick={() => info.data && copy(info.data.endpoint)}><Copy /></button></div>
            <div className="small muted">Claude, ChatGPT and other OAuth-capable clients: add this URL as a custom connector. You'll approve access with your password and choose read-only or read + write.</div>
          </div>
          <div className="card card-pad stack">
            <b>Agents with a token (Hermes, scripts)</b>
            <pre className="small" style={{ margin: 0, whiteSpace: "pre-wrap", wordBreak: "break-all" }}>{config}</pre>
            <div className="row"><button className="btn sm" onClick={() => copy(config)}><Copy />Copy config</button><span className="small muted">Limits: {info.data?.rate_limits.writes_per_min} writes / {info.data?.rate_limits.reads_per_min} reads per minute per agent</span></div>
          </div>
        </div>

        <h2 className="section">Connected agents</h2>
        <div className="card scroll-x">
          {creds.data?.length === 0 ? <Empty>No agents yet. Issue a token or connect an OAuth client.</Empty> : (
            <table className="tbl">
              <thead><tr><th>Agent</th><th>Access</th><th>Activity</th><th>Last seen</th><th /></tr></thead>
              <tbody>
                {creds.data?.map((c) => (
                  <tr key={c.client_id} style={c.revoked_at ? { opacity: 0.5 } : undefined}>
                    <td><b>{c.label}</b><div className="small muted">{c.kind === "env" ? "environment token" : c.kind === "oauth" ? "OAuth client" : "agent token"} · <code>{c.client_id.slice(0, 32)}</code></div></td>
                    <td><div className="row wrap" style={{ gap: 4 }}>{c.revoked_at ? <span className="chip bad">revoked</span> : c.scopes.map((s) => <span key={s} className={`chip ${s.endsWith("write") || s.endsWith("full") ? "warn" : ""}`}>{s.replace("knowledge:", "")}</span>)}</div></td>
                    <td className="small">{c.activity.actions ?? 0} calls · {c.activity.writes ?? 0} writes<div><Link to={`/activity${qs({ actor: `mcp:${c.client_id}` })}`}>view log</Link></div></td>
                    <td className="small muted">{timeAgo(c.activity.last_seen || c.last_used_at) || "never"}</td>
                    <td><div className="row" style={{ justifyContent: "flex-end", gap: 2 }}>
                      {c.kind !== "env" && <button className="btn ghost sm icon" title="Rename" onClick={() => rename(c)}><Pencil /></button>}
                      {c.revocable && c.kind !== "env" && <button className="btn ghost sm icon" title="Revoke" onClick={() => revoke(c)}><ShieldOff /></button>}
                    </div></td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
        {info.data?.static_write_token_configured && <p className="small muted">The environment token (MCP_AUTH_TOKEN) still works for existing agents. To retire it, give each agent its own token here, then remove the variable and restart.</p>}

        <h2 className="section">Tools agents can use</h2>
        <div className="card list">
          {info.data?.tools.map((t) => (
            <div key={t.name} className="list-item">
              <div className="row"><code>{t.name}</code>{t.scope.endsWith("write") && <span className="chip warn">write</span>}{t.destructive && <span className="chip bad">reversible delete</span>}</div>
              <div className="s">{t.description}</div>
            </div>
          ))}
        </div>
        {!!info.data?.legacy_aliases.length && <p className="small muted">Old tool names still accepted: {info.data.legacy_aliases.join(", ")}.</p>}
      </div>

      {issuing && (
        <Modal title="New agent token" onClose={() => setIssuing(false)} footer={<><button className="btn" onClick={() => setIssuing(false)}>Cancel</button><button className="btn primary" disabled={!label.trim()} onClick={issue}>Create</button></>}>
          <div className="stack">
            <label className="small">Agent name<input className="input" autoFocus placeholder="Hermes" value={label} onChange={(e) => setLabel(e.target.value)} style={{ marginTop: 6 }} /></label>
            <label className="row small"><input type="checkbox" checked={write} onChange={(e) => setWrite(e.target.checked)} />Allow writing notes and memories (deletes go to trash)</label>
          </div>
        </Modal>
      )}
      {issued && (
        <Modal title={`Token for ${issued.label}`} onClose={() => setIssued(null)} footer={<button className="btn primary" onClick={() => setIssued(null)}>Done</button>}>
          <p className="small" style={{ marginTop: 0 }}>Copy it now — it is stored hashed and cannot be shown again.</p>
          <div className="row"><code className="grow card card-pad" style={{ wordBreak: "break-all" }}>{issued.token}</code><button className="btn icon" onClick={() => copy(issued.token)}><Copy /></button></div>
        </Modal>
      )}
    </div>
  );
}
