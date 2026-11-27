import { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ChevronDown, ChevronRight, FileText, Plus, Search } from "lucide-react";
import { api, noteHref, qs } from "../lib/api";
import { useMemoryRefresh } from "../lib/hooks";
import type { Fact, MemoryHealth, RememberResult, Subject } from "../lib/types";
import FactRow from "../components/FactRow";
import { Empty, errorText, useDebounced, useDialog, useToast } from "../components/ui";

const GROUPS: { kind: Subject["kind"]; label: string }[] = [
  { kind: "project", label: "Projects" }, { kind: "client", label: "Clients" },
  { kind: "person", label: "People" }, { kind: "topic", label: "Topics" },
];
const CORE_BLURB: Record<string, string> = {
  "Memory/Profile.md": "Who you are. Every agent reads this first.",
  "Memory/Rules.md": "How agents should work for you. Every agent reads this first.",
};

export default function MemoryPage() {
  const [params, setParams] = useSearchParams();
  const view = params.get("view") ?? "subjects";
  const setView = (v: string) => setParams(v === "subjects" ? {} : { view: v });
  return (
    <div className="page">
      <div className="mem">
        <header className="mem-head">
          <div>
            <h1 className="title">Memory</h1>
            <p className="muted" style={{ margin: 0 }}>Short facts your agents share. Stored as Markdown in <code>Memory/</code>.</p>
          </div>
          <div className="seg">
            {[["subjects", "Subjects"], ["brief", "What agents see"], ["health", "Tidy-up"]].map(([k, l]) =>
              <button key={k} className={view === k ? "on" : ""} onClick={() => setView(k)}>{l}</button>)}
          </div>
        </header>
        {view === "brief" ? <BriefPreview /> : view === "health" ? <Health /> : <Subjects />}
      </div>
    </div>
  );
}

function Subjects() {
  const [params, setParams] = useSearchParams();
  const subjects = useQuery({ queryKey: ["memory", "subjects"], queryFn: () => api.get<Subject[]>("/api/memory/subjects") });
  const [filter, setFilter] = useState("");
  const dq = useDebounced(filter, 200);
  const found = useQuery({ queryKey: ["memory", "search", dq], queryFn: () => api.get<Fact[]>(`/api/memory/search${qs({ q: dq, limit: 40 })}`), enabled: dq.trim().length > 1 });
  const { prompt } = useDialog();
  const refresh = useMemoryRefresh();
  const toast = useToast();
  const list = subjects.data ?? [];
  const selected = params.get("s") ?? "Memory/Profile.md";
  const current = list.find((s) => s.path === selected);
  const [mobileList, setMobileList] = useState(!params.get("s"));

  const pick = (path: string) => { setParams({ s: path }); setMobileList(false); setFilter(""); };
  const newSubject = async (kind: Subject["kind"]) => {
    const name = await prompt(`New ${kind}`, { label: "Name", placeholder: kind === "person" ? "Ada Lovelace" : "EcomLab" });
    if (!name) return;
    try {
      const r = await api.post<{ path: string }>("/api/memory/subjects", { subject: name, kind });
      refresh();
      pick(r.path);
    } catch (e) { toast(errorText(e), { kind: "error" }); }
  };

  return (
    <div className={`mem-body ${mobileList ? "show-list" : ""}`}>
      <aside className="mem-rail">
        <label className="mem-filter"><Search size={15} /><input placeholder="Find a fact…" value={filter} onChange={(e) => setFilter(e.target.value)} /></label>
        <div className="rail-group">Given to every agent</div>
        {list.filter((s) => s.kind === "profile" || s.kind === "rules").map((s) => (
          <SubjectLink key={s.path} s={s} active={s.path === selected && !dq} onPick={pick} />
        ))}
        {GROUPS.map((g) => {
          const items = list.filter((s) => s.kind === g.kind);
          return (
            <div key={g.kind}>
              <div className="rail-group spread"><span>{g.label}</span>
                <button className="btn ghost sm icon" title={`New ${g.kind}`} onClick={() => newSubject(g.kind)}><Plus /></button></div>
              {items.length === 0 ? <div className="rail-empty">None yet</div>
                : items.map((s) => <SubjectLink key={s.path} s={s} active={s.path === selected && !dq} onPick={pick} />)}
            </div>
          );
        })}
      </aside>
      <section className="mem-detail">
        <button className="btn sm ghost hide-desktop" style={{ marginBottom: 12 }} onClick={() => setMobileList(true)}>← All subjects</button>
        {dq.trim().length > 1 ? (
          <>
            <h2 className="mem-title">Facts matching “{dq}”</h2>
            <div className="fact-list">
              {found.data?.length === 0 && <Empty>No facts match. Try other words.</Empty>}
              {found.data?.map((f) => <FactRow key={f.id} fact={f} showSubject />)}
            </div>
          </>
        ) : current ? <SubjectDetail s={current} /> : subjects.isLoading ? <Empty>Loading…</Empty> : <Empty>Pick a subject.</Empty>}
      </section>
    </div>
  );
}

function SubjectLink({ s, active, onPick }: { s: Subject; active: boolean; onPick: (p: string) => void }) {
  return (
    <button className={`subject-link ${active ? "on" : ""}`} onClick={() => onPick(s.path)}>
      <span className="grow">{s.subject}</span>
      {s.unreviewed > 0 && <span className="dot-new" title={`${s.unreviewed} to review`} />}
      <span className="n">{s.facts}</span>
    </button>
  );
}

function SubjectDetail({ s }: { s: Subject }) {
  const facts = useQuery({ queryKey: ["memory", "facts", s.path], queryFn: () => api.get<Fact[]>(`/api/memory/facts${qs({ path: s.path })}`) });
  const history = useQuery({ queryKey: ["memory", "history", s.path], queryFn: () => api.get<Fact[]>(`/api/memory/facts${qs({ path: s.path, status: "superseded" })}`) });
  const health = useQuery({ queryKey: ["memory", "health"], queryFn: () => api.get<MemoryHealth>("/api/memory/health"), enabled: s.kind === "profile" || s.kind === "rules" });
  const [text, setText] = useState("");
  const [similar, setSimilar] = useState<Fact[] | null>(null);
  const [showHist, setShowHist] = useState(false);
  const refresh = useMemoryRefresh();
  const toast = useToast();
  const core = health.data?.core[s.path];

  const add = async (force = false) => {
    if (!text.trim()) return;
    try {
      const r = await api.post<RememberResult>(force ? "/api/memory/facts/force" : "/api/memory/facts", { subject: s.path, fact: text });
      if (r.status === "needs_decision") return setSimilar(r.similar ?? []);
      if (r.status === "exists") toast("Already saved");
      else toast("Saved");
      setText(""); setSimilar(null); refresh();
    } catch (e) { toast(errorText(e), { kind: "error" }); }
  };
  const replace = async (id: string) => {
    await api.post("/api/memory/facts", { subject: s.path, fact: text, replaces: id });
    setText(""); setSimilar(null); refresh(); toast("Replaced");
  };

  return (
    <>
      <div className="spread" style={{ alignItems: "flex-start" }}>
        <div>
          <h2 className="mem-title">{s.subject}</h2>
          <p className="muted" style={{ margin: "2px 0 0" }}>{CORE_BLURB[s.path] ?? `${s.kind[0].toUpperCase()}${s.kind.slice(1)} · fetched when an agent's work is about it.`}</p>
        </div>
        <Link className="btn sm ghost" to={noteHref(s.path)}><FileText />Open file</Link>
      </div>
      {core && (
        <div className="meter" title={`${core.chars} of ${core.limit} characters`}>
          <div style={{ width: `${Math.min(100, (core.chars / core.limit) * 100)}%` }} className={core.chars > core.limit ? "over" : ""} />
          <span>{core.chars > core.limit ? "Too long — agents only get the first part. Trim it." : `${Math.round((core.chars / core.limit) * 100)}% of the space agents read at the start`}</span>
        </div>
      )}
      <form className="add-fact" onSubmit={(e) => { e.preventDefault(); add(); }}>
        <input className="input" placeholder={s.kind === "rules" ? "e.g. Log decisions with paths and URLs" : s.kind === "profile" ? "e.g. Works from Lagos (UTC+1)" : `A fact about ${s.subject}`}
               value={text} onChange={(e) => { setText(e.target.value); setSimilar(null); }} maxLength={600} />
        <button className="btn primary" disabled={!text.trim()}><Plus />Add</button>
      </form>
      {similar && (
        <div className="similar">
          <div className="small"><b>Something similar is already saved.</b> Replace it, or keep both.</div>
          {similar.map((f) => (
            <div key={f.id} className="row" style={{ justifyContent: "space-between" }}>
              <span className="small">{f.text} <span className="muted">— {f.subject}</span></span>
              <button className="btn sm" onClick={() => replace(f.id)}>Replace this</button>
            </div>
          ))}
          <div className="row"><button className="btn sm" onClick={() => add(true)}>Keep both</button><button className="btn sm ghost" onClick={() => setSimilar(null)}>Cancel</button></div>
        </div>
      )}
      <div className="fact-list">
        {facts.isLoading ? <Empty>Loading…</Empty> : facts.data?.length === 0
          ? <Empty>No facts yet. Add one above, or let an agent save them with <code>remember</code>.</Empty>
          : facts.data?.map((f) => <FactRow key={f.id} fact={f} />)}
      </div>
      {!!history.data?.length && (
        <div style={{ marginTop: 22 }}>
          <button className="btn ghost sm" onClick={() => setShowHist((v) => !v)}>{showHist ? <ChevronDown /> : <ChevronRight />}History ({history.data.length})</button>
          {showHist && <div className="fact-list">{history.data.map((f) => <FactRow key={f.id} fact={f} />)}</div>}
        </div>
      )}
    </>
  );
}

function BriefPreview() {
  const sys = useQuery({ queryKey: ["agents"], queryFn: () => api.get<{ label: string; revoked_at?: string | null }[]>("/api/agents") });
  const names = useMemo(() => Array.from(new Set(["Hermes", "Claude", ...(sys.data ?? []).filter((a) => !a.revoked_at).map((a) => a.label)])), [sys.data]);
  const [agent, setAgent] = useState("Hermes");
  const [topic, setTopic] = useState("");
  const dt = useDebounced(topic, 400);
  const brief = useQuery({ queryKey: ["memory", "brief", agent, dt], queryFn: () => api.get<{ brief: string; tokens: number; truncated: boolean }>(`/api/memory/brief${qs({ agent, topic: dt })}`) });
  return (
    <div className="mem-single">
      <p className="muted" style={{ marginTop: 0 }}>This is exactly what an agent gets when it calls <code>brief</code> at the start of a session.</p>
      <div className="row wrap" style={{ marginBottom: 14 }}>
        <select className="select" style={{ width: "auto" }} value={agent} onChange={(e) => setAgent(e.target.value)}>
          {names.map((n) => <option key={n}>{n}</option>)}
        </select>
        <input className="input grow" placeholder="Topic, e.g. EcomLab crawler (optional)" value={topic} onChange={(e) => setTopic(e.target.value)} />
      </div>
      <pre className="brief">{brief.data?.brief ?? "Loading…"}</pre>
      {brief.data && <div className="small muted">About {brief.data.tokens.toLocaleString()} tokens{brief.data.truncated ? ", cut to fit the budget" : ""}.</div>}
    </div>
  );
}

function Health() {
  const h = useQuery({ queryKey: ["memory", "health"], queryFn: () => api.get<MemoryHealth>("/api/memory/health") });
  if (!h.data) return <Empty>Checking…</Empty>;
  const d = h.data;
  return (
    <div className="mem-single">
      <p className="muted" style={{ marginTop: 0 }}>Things worth a look. Hermes can also work through this list with the “Memory tidy-up” skill.</p>
      <section className="health-block">
        <h3>Looks like an instruction <span className="muted">{d.flagged.length}</span></h3>
        <p className="small muted">Kept out of agent briefs until you decide. Usually text an agent copied from a web page.</p>
        <div className="fact-list">{d.flagged.length ? d.flagged.map((f) => <FactRow key={f.id} fact={f} showSubject />) : <div className="small muted">None.</div>}</div>
      </section>
      <section className="health-block">
        <h3>Possible duplicates <span className="muted">{d.duplicates.length}</span></h3>
        {d.duplicates.length ? d.duplicates.map((p, i) => (
          <div key={i} className="dupe"><FactRow fact={p.a} showSubject /><FactRow fact={p.b} showSubject /></div>
        )) : <div className="small muted">None.</div>}
      </section>
      <section className="health-block">
        <h3>Not updated in {d.stale_days} days <span className="muted">{d.stale.length}</span></h3>
        <div className="fact-list">{d.stale.length ? d.stale.slice(0, 30).map((f) => <FactRow key={f.id} fact={f} showSubject />) : <div className="small muted">Everything is recent.</div>}</div>
      </section>
    </div>
  );
}
