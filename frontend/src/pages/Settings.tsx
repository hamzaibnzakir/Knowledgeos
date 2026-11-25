import { useState } from "react";
import { Link } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, LogOut, RefreshCw, RotateCcw, Trash2 } from "lucide-react";
import { api, noteHref } from "../lib/api";
import { useSystem, useVaultRefresh } from "../lib/hooks";
import { bytes, Empty, errorText, timeAgo, useDialog, useTheme, useToast } from "../components/ui";

interface Usage {
  totals: { calls: number; input_tokens: number; output_tokens: number; cost_usd: number };
  by_model: { model: string; operation: string; calls: number; input_tokens: number; output_tokens: number; cost_usd: number; avg_latency_ms: number }[];
  by_day: { day: string; calls: number; tokens: number; cost_usd: number }[];
}

export default function SettingsPage() {
  const [tab, setTab] = useState("general");
  return (
    <div className="page">
      <div className="page-inner narrow">
        <h1 className="title">Settings</h1>
        <div className="tabs">
          {[["general", "General"], ["ai", "AI & usage"], ["data", "Data & backups"], ["trash", "Trash"], ["security", "Security"]].map(([k, l]) =>
            <button key={k} className={tab === k ? "on" : ""} onClick={() => setTab(k)}>{l}</button>)}
        </div>
        {tab === "general" && <General />}
        {tab === "ai" && <AiUsage />}
        {tab === "data" && <Data />}
        {tab === "trash" && <Trash />}
        {tab === "security" && <Security />}
      </div>
    </div>
  );
}

function General() {
  const [theme, setTheme] = useTheme();
  const sys = useSystem();
  const s = sys.data?.stats;
  return (
    <div className="stack">
      <div className="card card-pad spread"><div><b>Theme</b><div className="small muted">Follows your device unless you pick one.</div></div>
        <div className="seg">{["system", "light", "dark"].map((t) => <button key={t} className={theme === t ? "on" : ""} onClick={() => setTheme(t)}>{t}</button>)}</div></div>
      {s && <div className="grid cols-4">
        {[["notes", "Notes"], ["words", "Words"], ["links", "Links"], ["tags", "Tags"], ["memories", "Memories"], ["skills", "Skills"], ["chunks", "Indexed chunks"], ["unresolved_links", "Missing links"]].map(([k, l]) =>
          <div key={k} className="card stat"><b>{(s[k] ?? 0).toLocaleString()}</b><span>{l}</span></div>)}
      </div>}
      <div className="card card-pad small muted">Knowledge OS {sys.data?.version}. Keyboard: <kbd>⌘K</kbd> jump · <kbd>⌘⇧F</kbd> search · <kbd>Alt N</kbd> new note · <kbd>⌘S</kbd> save · double-click a note to edit.</div>
    </div>
  );
}

function AiUsage() {
  const sys = useSystem();
  const [days, setDays] = useState(30);
  const usage = useQuery({ queryKey: ["usage", days], queryFn: () => api.get<Usage>(`/api/usage?days=${days}`) });
  const toast = useToast();
  const ai = sys.data?.ai;
  const emb = sys.data?.embeddings;
  const maxTokens = Math.max(1, ...(usage.data?.by_day.map((d) => d.tokens) ?? [1]));
  return (
    <div className="stack">
      <div className="card card-pad stack">
        <div className="spread"><b>AI provider</b>{ai?.configured ? <span className="chip good">connected</span> : <span className="chip warn">not configured</span>}</div>
        <dl className="props small">
          <dt>Provider</dt><dd>{ai?.provider} <span className="muted">({ai?.base_url})</span></dd>
          <dt>Agent model</dt><dd>{ai?.agent_model}</dd>
          <dt>Chat model</dt><dd>{ai?.chat_model}</dd>
          <dt>Utility model</dt><dd>{ai?.utility_model}</dd>
          <dt>Context budget</dt><dd>{sys.data?.context_token_budget?.toLocaleString()} tokens per answer</dd>
        </dl>
        <div className="small muted">Change these with environment variables (KOS_AI_PROVIDER, KOS_AI_BASE_URL, KOS_AI_API_KEY, KOS_AGENT_MODEL…). Keys are never shown here.</div>
      </div>
      <div className="card card-pad stack">
        <div className="spread"><b>Semantic search</b>{emb?.available ? <span className="chip good">on</span> : <span className="chip">off</span>}</div>
        <div className="small">{emb?.provider} · {emb?.pending ? `${emb.pending} chunks waiting to be embedded${emb.running ? " (working…)" : ""}` : "all chunks embedded"}</div>
        {emb?.error && <div className="small" style={{ color: "var(--bad)" }}>{emb.error}</div>}
        {emb?.available && !!emb.pending && <button className="btn sm" style={{ alignSelf: "flex-start" }} onClick={async () => { await api.post("/api/embeddings/run"); toast("Embedding started"); }}>Embed now</button>}
      </div>
      <div className="card card-pad stack">
        <div className="spread"><b>Usage</b><div className="seg">{[7, 30, 90].map((d) => <button key={d} className={days === d ? "on" : ""} onClick={() => setDays(d)}>{d}d</button>)}</div></div>
        {usage.data && <>
          <div className="grid cols-4">
            <div className="stat card"><b>{usage.data.totals.calls}</b><span>API calls</span></div>
            <div className="stat card"><b>{(usage.data.totals.input_tokens / 1000).toFixed(1)}k</b><span>input tokens</span></div>
            <div className="stat card"><b>{(usage.data.totals.output_tokens / 1000).toFixed(1)}k</b><span>output tokens</span></div>
            <div className="stat card"><b>${usage.data.totals.cost_usd.toFixed(3)}</b><span>estimated cost</span></div>
          </div>
          {usage.data.by_day.length > 0 && <div className="row" style={{ alignItems: "flex-end", height: 70, gap: 2 }}>
            {usage.data.by_day.map((d) => <div key={d.day} title={`${d.day}: ${d.tokens} tokens, ${d.calls} calls`} style={{ flex: 1, background: "var(--accent)", opacity: 0.75, height: `${Math.max(3, (d.tokens / maxTokens) * 100)}%`, borderRadius: 2 }} />)}
          </div>}
          <div className="scroll-x"><table className="tbl"><thead><tr><th>Model</th><th>Operation</th><th>Calls</th><th>Tokens in / out</th><th>Cost</th><th>Avg latency</th></tr></thead>
            <tbody>{usage.data.by_model.map((r, i) => <tr key={i}><td>{r.model}</td><td>{r.operation}</td><td>{r.calls}</td><td>{r.input_tokens?.toLocaleString()} / {r.output_tokens?.toLocaleString()}</td><td>${(r.cost_usd ?? 0).toFixed(4)}</td><td>{r.avg_latency_ms} ms</td></tr>)}</tbody></table></div>
          {usage.data.by_model.length === 0 && <Empty>No AI calls in this period.</Empty>}
          <div className="small muted">Costs are estimates from a built-in price table (override with KOS_PRICING_JSON).</div>
        </>}
      </div>
    </div>
  );
}

function Data() {
  const backups = useQuery({ queryKey: ["backups"], queryFn: () => api.get<{ name: string; bytes: number; created_at: string }[]>("/api/backups") });
  const sys = useSystem();
  const toast = useToast();
  const refresh = useVaultRefresh();
  const [busy, setBusy] = useState("");
  const run = async (what: string, fn: () => Promise<unknown>) => { setBusy(what); try { await fn(); } catch (e) { toast(errorText(e), { kind: "error" }); } finally { setBusy(""); } };
  return (
    <div className="stack">
      <div className="card card-pad stack">
        <b>Your data</b>
        <div className="small muted">Notes are plain Markdown files in the vault folder — open them in Obsidian or any editor. The database only holds rebuildable indexes plus memories, chats and history.</div>
        <div className="row wrap">
          <a className="btn" href="/api/export"><Download />Download vault (.zip)</a>
          <button className="btn" disabled={!!busy} onClick={() => run("scan", async () => { const r = await api.post<Record<string, number>>("/api/reindex", { full: false }); refresh(); toast(`Indexed ${r.indexed}, removed ${r.removed}`); })}><RefreshCw />Scan for outside changes</button>
          <button className="btn" disabled={!!busy} onClick={() => run("full", async () => { const r = await api.post<Record<string, number>>("/api/reindex", { full: true }); refresh(); toast(`Rebuilt index for ${r.indexed} notes`); })}>{busy === "full" ? "Rebuilding…" : "Rebuild index"}</button>
        </div>
      </div>
      <div className="card card-pad stack">
        <div className="spread"><div><b>Backups</b><div className="small muted">Vault + database snapshot. Automatic every {sys.data?.limits.backup_interval_hours}h, keeping {sys.data?.limits.backup_keep}. Last: {sys.data?.last_backup ?? "never"}.</div></div>
          <button className="btn primary" disabled={!!busy} onClick={() => run("backup", async () => { await api.post("/api/backups"); backups.refetch(); toast("Backup created"); })}>{busy === "backup" ? "Working…" : "Back up now"}</button></div>
        <div className="list">
          {backups.data?.length === 0 && <Empty>No backups yet.</Empty>}
          {backups.data?.map((b) => <div key={b.name} className="list-item spread"><span><code className="small">{b.name}</code> <span className="muted small">{bytes(b.bytes)}</span></span><a className="btn sm" href={`/api/backups/${b.name}`}><Download />Download</a></div>)}
        </div>
      </div>
    </div>
  );
}

function Trash() {
  const qc = useQueryClient();
  const refresh = useVaultRefresh();
  const toast = useToast();
  const { confirm } = useDialog();
  const trash = useQuery({ queryKey: ["trash"], queryFn: () => api.get<{ id: string; path: string; actor: string; deleted_at: string }[]>("/api/trash") });
  return (
    <div className="card list">
      {trash.data?.length === 0 && <Empty>Trash is empty.</Empty>}
      {trash.data?.map((t) => (
        <div key={t.id} className="list-item spread">
          <div><div>{t.path}</div><div className="small muted">deleted {timeAgo(t.deleted_at)} by {t.actor}</div></div>
          <div className="row">
            <button className="btn sm" onClick={async () => { const r = await api.post<{ path: string }>(`/api/trash/${t.id}/restore`); refresh(); qc.invalidateQueries({ queryKey: ["trash"] }); toast("Restored", { action: { label: "Open", run: () => { window.location.href = noteHref(r.path); } } }); }}><RotateCcw />Restore</button>
            <button className="btn sm danger" onClick={async () => { if (await confirm("Delete forever?", { body: t.path, danger: true, confirm: "Delete forever" })) { await api.del(`/api/trash/${t.id}`); qc.invalidateQueries({ queryKey: ["trash"] }); } }}><Trash2 /></button>
          </div>
        </div>
      ))}
    </div>
  );
}

function Security() {
  const sessions = useQuery({ queryKey: ["sessions"], queryFn: () => api.get<{ id: string; created_at: string; last_seen: string; ip: string; user_agent: string; current: boolean }[]>("/api/auth/sessions") });
  const qc = useQueryClient();
  const toast = useToast();
  const { confirm } = useDialog();
  const [cur, setCur] = useState("");
  const [next, setNext] = useState("");
  const logout = async () => { await api.post("/api/auth/logout"); qc.setQueryData(["auth"], { authenticated: false, password_configured: true }); };
  return (
    <div className="stack">
      <div className="card card-pad stack">
        <div className="spread"><b>Signed-in devices</b><div className="row">
          <button className="btn sm" onClick={logout}><LogOut />Sign out</button>
          <button className="btn sm danger" onClick={async () => { if (await confirm("Sign out everywhere?", { danger: true, confirm: "Sign out all" })) { await api.post("/api/auth/sessions/revoke-all"); qc.setQueryData(["auth"], { authenticated: false, password_configured: true }); } }}>Sign out everywhere</button>
        </div></div>
        <div className="list">{sessions.data?.map((s) => <div key={s.id} className="list-item"><div>{s.user_agent.slice(0, 80) || "Unknown device"} {s.current && <span className="chip accent">this device</span>}</div><div className="small muted">{s.ip} · last active {timeAgo(s.last_seen)}</div></div>)}</div>
      </div>
      <form className="card card-pad stack" onSubmit={async (e) => { e.preventDefault(); try { await api.post("/api/auth/password", { current: cur, new: next }); toast("Password changed — sign in again"); qc.setQueryData(["auth"], { authenticated: false, password_configured: true }); } catch (ex) { toast(errorText(ex), { kind: "error" }); } }}>
        <b>Change password</b>
        <div className="small muted">Also used to approve new MCP connections. A long passphrase is safer than a short code.</div>
        <input className="input" type="password" autoComplete="current-password" placeholder="Current password" value={cur} onChange={(e) => setCur(e.target.value)} />
        <input className="input" type="password" autoComplete="new-password" placeholder="New password (6+ characters)" value={next} onChange={(e) => setNext(e.target.value)} />
        <button className="btn primary" style={{ alignSelf: "flex-start" }} disabled={!cur || next.length < 6}>Change password</button>
      </form>
      <div className="card card-pad small">Agent access is managed on <Link to="/agents">Agents &amp; MCP</Link>.</div>
    </div>
  );
}
