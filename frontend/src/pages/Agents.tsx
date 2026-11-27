import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { AnimatePresence, motion } from "motion/react";
import { Check, ChevronDown, Copy, Eye, FileText, Pencil, Plug, ScrollText, ShieldCheck, ShieldOff, Sparkles, TriangleAlert } from "lucide-react";
import { api, noteHref, qs } from "../lib/api";
import type { AuditEvent, Credential } from "../lib/types";
import { describe } from "../lib/words";
import { Cascade, CountUp, rise } from "../components/motion";
import { errorText, Modal, timeAgo, useDialog, useToast } from "../components/ui";

interface McpInfo {
  endpoint: string;
  oauth: { issuer: string; metadata: string; scopes: string[] };
  static_write_token_configured: boolean;
  rate_limits: { writes_per_min: number; reads_per_min: number };
  tools: { name: string; description: string; scope: string; destructive: boolean }[];
  legacy_aliases: string[];
}

const AGENT_COLORS = ["#2e4bff", "#ff6a4d", "#12a57a", "#8a5cff", "#e0a100", "#0e9fb8", "#d9468f"];
export function agentColor(name: string) {
  let h = 0;
  for (const c of name.toLowerCase()) h = (h * 31 + c.charCodeAt(0)) >>> 0;
  return AGENT_COLORS[h % AGENT_COLORS.length];
}

const TRUST: { key: Credential["trust"]; label: string; icon: typeof Eye; blurb: string }[] = [
  { key: "read", label: "Read only", icon: Eye, blurb: "Can search and read. Cannot change anything." },
  { key: "review", label: "Reviewed", icon: ShieldCheck, blurb: "Can write. Every fact it saves waits in Review for you." },
  { key: "trusted", label: "Trusted", icon: Sparkles, blurb: "Its facts skip Review. Profile and Rules still need your approval." },
];

const TOOL_GROUPS: [string, (n: string) => boolean][] = [
  ["Memory", (n) => ["brief", "recall", "remember", "revise", "forget", "list_facts", "list_subjects", "memory_health", "log"].includes(n)],
  ["Search & context", (n) => /search|retrieve|related|context/.test(n)],
  ["Notes", (n) => /note|append|replace|properties|move|delete/.test(n)],
  ["Links & graph", (n) => /link|graph|backlink|tag/.test(n)],
  ["Skills & other", () => true],
];

const isLive = (iso?: string | null) => !!iso && Date.now() - Date.parse(iso) < 15 * 60_000;

export default function AgentsPage() {
  const info = useQuery({ queryKey: ["mcp"], queryFn: () => api.get<McpInfo>("/api/mcp") });
  const creds = useQuery({ queryKey: ["agents"], queryFn: () => api.get<Credential[]>("/api/agents") });
  const audit = useQuery({ queryKey: ["audit", "agents"], queryFn: () => api.get<AuditEvent[]>("/api/audit?limit=200") });
  const [connect, setConnect] = useState(false);
  const [showTools, setShowTools] = useState(false);

  const active = (creds.data ?? []).filter((c) => !c.revoked_at);
  const removed = (creds.data ?? []).filter((c) => c.revoked_at);
  const byClient = useMemo(() => Object.fromEntries((creds.data ?? []).map((c) => [`mcp:${c.client_id}`, c])), [creds.data]);
  // Collapse runs of the same thing ("Claude saved a fact in Moufie Wear" ×4) into one line.
  const timeline = useMemo(() => {
    const out: { e: AuditEvent; n: number }[] = [];
    for (const e of (audit.data ?? []).filter((x) => x.actor.startsWith("mcp:") && byClient[x.actor])) {
      const last = out[out.length - 1];
      if (last && last.e.actor === e.actor && last.e.action === e.action && last.e.details.path === e.details.path) last.n++;
      else out.push({ e, n: 1 });
    }
    return out.slice(0, 10);
  }, [audit.data, byClient]);
  const groups = useMemo(() => {
    const out: Record<string, McpInfo["tools"]> = {};
    for (const t of info.data?.tools ?? []) {
      const g = TOOL_GROUPS.find(([, f]) => f(t.name))![0];
      (out[g] ??= []).push(t);
    }
    return out;
  }, [info.data]);

  return (
    <div className="page">
      <div className="agents">
        <header className="mem-head">
          <div>
            <h1 className="title">Agents</h1>
            <p className="muted" style={{ margin: 0 }}>Who can reach your vault, how far you trust them, and what they did this week.</p>
          </div>
          <button className="btn primary" onClick={() => setConnect(true)}><Plug />Connect an agent</button>
        </header>

        {creds.isLoading ? <div className="empty">Loading…</div> : active.length === 0 ? (
          <div className="tile agents-empty">
            <h2>No agents yet</h2>
            <p className="muted">Connect Claude, ChatGPT, Hermes or a script. They share one memory and you decide how far to trust each.</p>
            <button className="btn primary" onClick={() => setConnect(true)}><Plug />Connect your first agent</button>
          </div>
        ) : (
          <Cascade className="agent-grid">
            {active.map((c) => <AgentCard key={c.client_id} c={c} />)}
          </Cascade>
        )}

        <div className="agents-lower">
          <section className="tile">
            <div className="tile-head"><h2>What agents did</h2><Link to="/activity">Full log</Link></div>
            {timeline.length === 0 ? <p className="muted">Nothing yet. Agent actions show up here as they happen.</p> : (
              <ol className="timeline">
                {timeline.map(({ e, n }) => {
                  const c = byClient[e.actor];
                  const path = typeof e.details.path === "string" ? e.details.path : null;
                  return (
                    <li key={e.id}>
                      <span className="tl-dot" style={{ background: agentColor(c.name) }} />
                      <div>
                        <b>{c.name}</b> {describe(e.action).toLowerCase()}{" "}
                        {path && <Link to={noteHref(path)}>{path.split("/").pop()!.replace(/\.md$/, "")}</Link>}
                        {n > 1 && <span className="chip" style={{ marginLeft: 6 }}>×{n}</span>}
                        <div className="small muted">{timeAgo(e.created_at)}</div>
                      </div>
                    </li>
                  );
                })}
              </ol>
            )}
          </section>

          <section className="tile">
            <div className="tile-head"><h2>What agents can do</h2>
              <button className="btn ghost sm" onClick={() => setShowTools((v) => !v)}>{showTools ? "Hide tools" : `Show ${info.data?.tools.length ?? ""} tools`}
                <motion.span animate={{ rotate: showTools ? 180 : 0 }} style={{ display: "inline-grid" }}><ChevronDown /></motion.span></button>
            </div>
            <p className="muted small" style={{ marginTop: 0 }}>Every agent gets the same tools. Writes are limited to {info.data?.rate_limits.writes_per_min ?? "…"} a minute, deletes go to a restorable trash, and every change is logged with the agent's name.</p>
            <div className="tool-groups">
              {Object.entries(groups).map(([g, list]) => <span key={g} className="chip">{g} <b>{list.length}</b></span>)}
            </div>
            <AnimatePresence initial={false}>
              {showTools && (
                <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: "auto", opacity: 1 }} exit={{ height: 0, opacity: 0 }}
                  transition={{ duration: 0.35, ease: [0.22, 1, 0.36, 1] }} style={{ overflow: "hidden" }}>
                  {Object.entries(groups).map(([g, list]) => (
                    <div key={g} className="tool-group">
                      <h3>{g}</h3>
                      {list.map((t) => (
                        <div key={t.name} className="tool-row">
                          <code>{t.name}</code>{t.scope.endsWith("write") && <span className="chip warn">writes</span>}
                          <div className="small muted">{t.description}</div>
                        </div>
                      ))}
                    </div>
                  ))}
                  {!!info.data?.legacy_aliases.length && <p className="small muted">Old names still accepted: {info.data.legacy_aliases.join(", ")}.</p>}
                </motion.div>
              )}
            </AnimatePresence>
          </section>
        </div>

        {removed.length > 0 && (
          <details className="removed">
            <summary>{removed.length} removed agent{removed.length === 1 ? "" : "s"}</summary>
            {removed.map((c) => <div key={c.client_id} className="small muted">{c.name || c.label} · removed {timeAgo(c.revoked_at)}</div>)}
          </details>
        )}
      </div>
      <AnimatePresence>{connect && info.data && <ConnectSheet info={info.data} onClose={() => setConnect(false)} />}</AnimatePresence>
    </div>
  );
}

function AgentCard({ c }: { c: Credential }) {
  const qc = useQueryClient();
  const toast = useToast();
  const { prompt, confirm } = useDialog();
  const color = agentColor(c.name);
  const canWrite = c.scopes.some((s) => s.endsWith("write") || s.endsWith("full"));
  const w = c.memory_week;
  const decided = w.kept + w.rejected;
  const live = isLive(c.activity.last_seen);
  const refresh = () => { qc.invalidateQueries({ queryKey: ["agents"] }); qc.invalidateQueries({ queryKey: ["system"] }); };

  const setPolicy = async (body: { trust?: Credential["trust"]; paused?: boolean }) => {
    try {
      await api.put("/api/agents/policy", { client_id: c.client_id, ...body });
      refresh();
      toast(body.paused === true ? `${c.name} paused` : body.paused === false ? `${c.name} resumed` : `${c.name} is now ${TRUST.find((t) => t.key === body.trust)?.label.toLowerCase()}`);
    } catch (e) { toast(errorText(e), { kind: "error" }); }
  };
  const rename = async () => {
    const v = await prompt("Rename agent", { value: c.name });
    if (v) { await api.patch(`/api/agents/${encodeURIComponent(c.client_id)}`, { label: v }); refresh(); }
  };
  const revoke = async () => {
    if (!(await confirm(`Remove ${c.name}?`, { body: "It loses access immediately and needs new credentials to come back. Its facts and notes stay.", danger: true, confirm: "Remove" }))) return;
    await api.post(`/api/agents/${encodeURIComponent(c.client_id)}/revoke`);
    refresh();
  };

  return (
    <motion.article variants={rise} layout className={`tile agent-card ${c.paused ? "paused" : ""}`} style={{ ["--agent" as string]: color }}>
      <div className="agent-top">
        <div className="avatar" aria-hidden>{c.name.slice(0, 1).toUpperCase()}{live && !c.paused && <span className="live" />}</div>
        <div className="grow">
          <h2 className="agent-name">{c.name}</h2>
          <div className="small muted">
            {c.kind === "oauth" ? "Connected with OAuth" : c.kind === "env" ? "Server token" : "Agent token"} ·{" "}
            {c.paused ? "paused" : live ? "active now" : c.activity.last_seen ? `seen ${timeAgo(c.activity.last_seen)}` : "not used yet"}
          </div>
        </div>
        <button className={`switch ${c.paused ? "" : "on"}`} role="switch" aria-checked={!c.paused} title={c.paused ? "Resume access" : "Pause access"}
            onClick={() => setPolicy({ paused: !c.paused })}><motion.span layout transition={{ type: "spring", stiffness: 600, damping: 32 }} /></button>
      </div>

      <div className="trust" role="radiogroup" aria-label={`How much to trust ${c.name}`}>
        {TRUST.map((t) => {
          const disabled = t.key !== "read" && !canWrite;
          const on = c.trust === t.key;
          return (
            <button key={t.key} role="radio" aria-checked={on} disabled={disabled || c.paused} className={on ? "on" : ""}
              title={disabled ? "This connection was granted read access only" : t.blurb} onClick={() => !on && setPolicy({ trust: t.key })}>
              {on && <motion.span layoutId={`trust-${c.client_id}`} className="trust-pill" transition={{ type: "spring", stiffness: 500, damping: 34 }} />}
              <t.icon size={14} />{t.label}
            </button>
          );
        })}
      </div>
      <p className="small muted trust-blurb">{c.paused ? "Paused. Every call is refused until you switch it back on." : TRUST.find((t) => t.key === c.trust)?.blurb}</p>

      <div className="week">
        <div><CountUp value={w.saved} className="num" /><span>facts saved</span></div>
        <div><CountUp value={w.kept} className="num" /><span>kept</span></div>
        <div><CountUp value={w.waiting + w.proposals} className="num" /><span>waiting</span></div>
        <div><CountUp value={c.notes_week} className="num" /><span>notes edited</span></div>
      </div>
      {decided > 0 && (
        <div className="keep-bar" title={`${w.kept} kept, ${w.rejected} rejected this week`}>
          <motion.div className="kept" initial={{ width: 0 }} animate={{ width: `${(w.kept / decided) * 100}%` }} transition={{ duration: 0.9, ease: [0.22, 1, 0.36, 1], delay: 0.3 }} />
          <span>{Math.round((w.kept / decided) * 100)}% of its facts kept this week{w.rejected ? ` · ${w.rejected} rejected` : ""}</span>
        </div>
      )}

      <div className={`habit ${c.activity.last_brief ? "good" : "warn"}`}>
        {c.activity.last_brief ? <><Check size={15} />Starts sessions with <code>brief</code> · last {timeAgo(c.activity.last_brief)}</>
          : <><TriangleAlert size={15} />Hasn't called <code>brief</code> yet. Ask it to call brief at the start of every session.</>}
      </div>

      <div className="agent-foot">
        <Link className="btn sm ghost" to={noteHref(`Agents/${c.name}/AGENT.md`)}><FileText />Profile</Link>
        <Link className="btn sm ghost" to={`/activity${qs({ actor: `mcp:${c.client_id}` })}`}><ScrollText />Activity</Link>
        <span className="grow" />
        {c.kind !== "env" && <button className="btn sm ghost icon" title="Rename" onClick={rename}><Pencil /></button>}
        {c.revocable && c.kind !== "env" && <button className="btn sm ghost icon" title="Remove access" onClick={revoke}><ShieldOff /></button>}
      </div>
    </motion.article>
  );
}

const CLIENTS = ["Claude", "ChatGPT", "Claude Code", "Hermes or a script"] as const;

function ConnectSheet({ info, onClose }: { info: McpInfo; onClose: () => void }) {
  const [tab, setTab] = useState<(typeof CLIENTS)[number]>("Claude");
  const [name, setName] = useState("");
  const [write, setWrite] = useState(true);
  const [token, setToken] = useState<string | null>(null);
  const qc = useQueryClient();
  const toast = useToast();
  const copy = (v: string) => navigator.clipboard.writeText(v).then(() => toast("Copied"), () => toast("Copy failed", { kind: "error" }));
  const needsToken = tab === "Claude Code" || tab === "Hermes or a script";
  const issue = async () => {
    try {
      const r = await api.post<{ token: string }>("/api/agents/tokens", { label: name.trim(), scopes: write ? ["knowledge:read", "knowledge:write"] : ["knowledge:read"] });
      setToken(r.token);
      qc.invalidateQueries({ queryKey: ["agents"] });
    } catch (e) { toast(errorText(e), { kind: "error" }); }
  };
  const tk = token ?? "<token>";
  const cmd = `claude mcp add --transport http knowledge-os ${info.endpoint} --header "Authorization: Bearer ${tk}"`;
  const json = JSON.stringify({ mcpServers: { "knowledge-os": { type: "http", url: info.endpoint, headers: { Authorization: `Bearer ${tk}` } } } }, null, 2);

  return (
    <Modal title="Connect an agent" onClose={onClose} wide>
      <div className="seg" style={{ marginBottom: 18, flexWrap: "wrap" }}>
        {CLIENTS.map((c) => <button key={c} className={tab === c ? "on" : ""} onClick={() => { setTab(c); setToken(null); }}>{c}</button>)}
      </div>
      <motion.div key={tab} initial={{ opacity: 0, x: 12 }} animate={{ opacity: 1, x: 0 }} transition={{ duration: 0.25 }}>
        {!needsToken ? (
          <ol className="steps">
            <li>Copy your Knowledge OS address.
              <div className="copy-row"><code>{info.endpoint}</code><button className="btn sm" onClick={() => copy(info.endpoint)}><Copy />Copy</button></div></li>
            {tab === "Claude"
              ? <li>In Claude, open <b>Settings → Connectors</b>, choose <b>Add custom connector</b> and paste the address.</li>
              : <li>In ChatGPT, open <b>Settings → Connectors</b> (developer mode may need to be on), create a connector and paste the address. Choose OAuth.</li>}
            <li>A Knowledge OS page opens. Enter your password and choose read-only or read and write.</li>
            <li>It appears here as a card. Set how much to trust it, and tell it to call <code>brief</code> at the start of each chat.</li>
          </ol>
        ) : !token ? (
          <div className="stack">
            <p className="muted" style={{ margin: 0 }}>{tab === "Claude Code" ? "Claude Code connects with a token." : "Hermes and scripts connect with a token."} Give it a name; that name appears on everything it saves.</p>
            <input className="input" autoFocus placeholder={tab === "Claude Code" ? "Claude Code" : "Hermes"} value={name} onChange={(e) => setName(e.target.value)} />
            <label className="row small"><input type="checkbox" checked={write} onChange={(e) => setWrite(e.target.checked)} />Allow it to write notes and memory</label>
            <div><button className="btn primary" disabled={!name.trim()} onClick={issue}>Create token</button></div>
          </div>
        ) : (
          <ol className="steps">
            <li>Here is {name}'s token. Copy it now; it is stored scrambled and can't be shown again.
              <div className="copy-row"><code>{token}</code><button className="btn sm" onClick={() => copy(token)}><Copy />Copy</button></div></li>
            {tab === "Claude Code"
              ? <li>Run this in a terminal:<div className="copy-row"><code>{cmd}</code><button className="btn sm" onClick={() => copy(cmd)}><Copy />Copy</button></div></li>
              : <li>Add this to the agent's MCP config:<pre className="brief" style={{ maxHeight: 220, margin: "8px 0" }}>{json}</pre><button className="btn sm" onClick={() => copy(json)}><Copy />Copy config</button></li>}
            <li>Tell it to call <code>brief</code> at the start of every session, save facts with <code>remember</code>, and record work with <code>log</code>.</li>
          </ol>
        )}
      </motion.div>
    </Modal>
  );
}
