import { useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { AnimatePresence, motion } from "motion/react";
import { Check, CheckCheck, X } from "lucide-react";
import { api, qs } from "../lib/api";
import { useMemoryRefresh } from "../lib/hooks";
import type { Fact, Proposal } from "../lib/types";
import FactRow from "../components/FactRow";
import { errorText, timeAgo, useToast } from "../components/ui";

const OP_WORDS = { add: "wants to add", replace: "wants to change", retire: "wants to remove" } as const;

export default function ReviewPage() {
  const q = useQuery({ queryKey: ["memory", "review"], queryFn: () => api.get<{ facts: Fact[]; proposals: Proposal[]; count: number }>("/api/memory/review") });
  const trusted = useQuery({ queryKey: ["memory", "trusted-recent"], queryFn: () => api.get<Fact[]>("/api/memory/trusted-recent") });
  const [showTrusted, setShowTrusted] = useState(false);
  const refresh = useMemoryRefresh();
  const toast = useToast();
  const facts = q.data?.facts ?? [];
  const proposals = q.data?.proposals ?? [];
  const groups = facts.reduce<Record<string, Fact[]>>((acc, f) => { (acc[f.path] ??= []).push(f); return acc; }, {});

  const approveAll = async (list: Fact[]) => {
    try {
      for (const f of list.filter((x) => !x.flag)) await api.post(`/api/memory/facts/${f.id}/approve`);
      toast(`Kept ${list.filter((x) => !x.flag).length}`);
      refresh();
    } catch (e) { toast(errorText(e), { kind: "error" }); }
  };

  return (
    <div className="page">
      <div className="mem mem-narrow">
        <header className="mem-head">
          <div>
            <h1 className="title">Review</h1>
            <p className="muted" style={{ margin: 0 }}>What your agents saved or want to change. Profile and Rules never change until you approve.</p>
          </div>
        </header>

        {q.isLoading ? null : q.data?.count === 0 ? (
          <motion.div className="all-clear" initial={{ opacity: 0, scale: 0.94 }} animate={{ opacity: 1, scale: 1 }} transition={{ type: "spring", stiffness: 260, damping: 18 }}>
            <motion.span initial={{ rotate: -30, scale: 0 }} animate={{ rotate: 0, scale: 1 }} transition={{ type: "spring", stiffness: 300, damping: 12, delay: 0.15 }}><CheckCheck size={34} /></motion.span>
            <div><b>All clear.</b><div className="muted">New facts from agents will show up here.</div></div>
          </motion.div>
        ) : null}

        {proposals.length > 0 && (
          <section className="review-section">
            <h2>Changes to Profile and Rules</h2>
            <AnimatePresence initial={false}>{proposals.map((p) => <ProposalCard key={p.id} p={p} />)}</AnimatePresence>
          </section>
        )}

        {Object.keys(groups).length > 0 && (
          <section className="review-section">
            <h2>New facts from agents</h2>
            <p className="small muted" style={{ marginTop: -4 }}>These are already visible to agents, marked as unreviewed. Keep them, fix them, or reject them.</p>
            {Object.entries(groups).map(([path, list]) => (
              <div key={path} className="review-group">
                <div className="spread">
                  <Link to={`/memory${qs({ s: path })}`} className="review-subject">{list[0].subject}</Link>
                  {list.filter((x) => !x.flag).length > 1 && <button className="btn sm" onClick={() => approveAll(list)}><Check />Keep all {list.filter((x) => !x.flag).length}</button>}
                </div>
                <div className="fact-list"><AnimatePresence initial={false}>{list.map((f) => <FactRow key={f.id} fact={f} />)}</AnimatePresence></div>
              </div>
            ))}
          </section>
        )}
        {!!trusted.data?.length && (
          <section className="review-section">
            <div className="spread" style={{ alignItems: "baseline" }}>
              <h2>Saved by trusted agents</h2>
              <button className="btn sm ghost" onClick={() => setShowTrusted((v) => !v)}>{showTrusted ? "Hide" : `Show ${trusted.data.length}`}</button>
            </div>
            <p className="small muted" style={{ marginTop: -4 }}>These skipped review this week because you trust the agent. Glance over them; retire anything wrong.</p>
            <AnimatePresence initial={false}>
              {showTrusted && (
                <motion.div className="review-group" initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: "auto" }} exit={{ opacity: 0, height: 0 }}
                  transition={{ duration: 0.35, ease: [0.22, 1, 0.36, 1] }} style={{ overflow: "hidden" }}>
                  <div className="fact-list"><AnimatePresence initial={false}>{trusted.data.map((f) => <FactRow key={f.id} fact={f} showSubject />)}</AnimatePresence></div>
                </motion.div>
              )}
            </AnimatePresence>
          </section>
        )}
      </div>
    </div>
  );
}

function ProposalCard({ p }: { p: Proposal }) {
  const [text, setText] = useState(p.text);
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const refresh = useMemoryRefresh();
  const toast = useToast();
  const target = p.path.endsWith("Rules.md") ? "Rules" : "Profile";
  const decide = async (approve: boolean) => {
    setBusy(true);
    try {
      await api.post(`/api/memory/proposals/${p.id}`, { approve, text: approve && editing ? text : null });
      toast(approve ? `${target} updated` : "Rejected");
      refresh();
    } catch (e) { toast(errorText(e), { kind: "error" }); } finally { setBusy(false); }
  };
  return (
    <motion.div layout className={`proposal ${p.flag ? "flagged" : ""}`} initial={{ opacity: 0, y: 14 }} animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, scale: 0.96, height: 0, marginBottom: 0, paddingTop: 0, paddingBottom: 0 }} transition={{ type: "spring", stiffness: 320, damping: 30 }}>
      <div className="proposal-who"><b>{p.agent}</b> {OP_WORDS[p.op]} <b>{target}</b> <span className="muted">· {timeAgo(p.created_at)}</span></div>
      {p.op !== "add" && p.current_text && <div className="proposal-old">{p.current_text}</div>}
      {p.op !== "retire" && (editing
        ? <input className="input" value={text} onChange={(e) => setText(e.target.value)} autoFocus maxLength={600} />
        : <div className="proposal-new">{p.text}</div>)}
      <div className="fact-meta">
        {p.flag && <span className="tag-flag">Looks like an instruction</span>}
        {p.external && <span className="tag-ext">From a web page or document</span>}
        {p.src && <span>Source: {p.src}</span>}
        {p.reason && <span>Reason: {p.reason}</span>}
      </div>
      <div className="row" style={{ marginTop: 10 }}>
        <button className="btn sm primary" disabled={busy} onClick={() => decide(true)}><Check />{editing ? "Save and approve" : "Approve"}</button>
        {p.op !== "retire" && !editing && <button className="btn sm" onClick={() => setEditing(true)}>Edit first</button>}
        <button className="btn sm ghost" disabled={busy} onClick={() => decide(false)}><X />Reject</button>
      </div>
    </motion.div>
  );
}
