import { useState } from "react";
import { Check, Pencil, Undo2, X } from "lucide-react";
import { api } from "../lib/api";
import { useMemoryRefresh } from "../lib/hooks";
import type { Fact } from "../lib/types";
import { dayLabel, errorText, useToast } from "./ui";

/** One memory fact: the statement first, provenance underneath, actions on the right. */
export default function FactRow({ fact, showSubject = false }: { fact: Fact; showSubject?: boolean }) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(fact.text);
  const [busy, setBusy] = useState(false);
  const refresh = useMemoryRefresh();
  const toast = useToast();
  const old = fact.status !== "current";

  const run = async (fn: () => Promise<unknown>, done: string) => {
    setBusy(true);
    try { await fn(); toast(done); refresh(); } catch (e) { toast(errorText(e), { kind: "error" }); } finally { setBusy(false); }
  };
  const save = () => run(async () => {
    await api.patch(`/api/memory/facts/${fact.id}`, { fact: text, in_place: fact.review === "new" });
    if (fact.review === "new") await api.post(`/api/memory/facts/${fact.id}/approve`);
    setEditing(false);
  }, "Fact updated");

  const by = !fact.by_agent || fact.by_agent === "you" ? "You" : fact.by_agent;
  return (
    <div className={`fact ${old ? "old" : ""} ${fact.review === "new" ? "unreviewed" : ""} ${fact.flag ? "flagged" : ""}`}>
      <div className="fact-main">
        {editing ? (
          <form onSubmit={(e) => { e.preventDefault(); save(); }} className="row">
            <input className="input" autoFocus value={text} onChange={(e) => setText(e.target.value)} maxLength={600} />
            <button className="btn sm primary" disabled={busy || !text.trim()}>Save</button>
            <button type="button" className="btn sm ghost" onClick={() => { setEditing(false); setText(fact.text); }}>Cancel</button>
          </form>
        ) : <div className="fact-text">{fact.text}</div>}
        <div className="fact-meta">
          {showSubject && <span className="fact-subject">{fact.subject}</span>}
          <span>{by}{fact.on_date ? `, ${dayLabel(fact.on_date)}` : ""}</span>
          {fact.review === "new" && !old && <span className="tag-new">Needs review</span>}
          {fact.flag && <span className="tag-flag">Looks like an instruction</span>}
          {fact.external && <span className="tag-ext">From a web page or document</span>}
          {fact.src && <span className="fact-src" title={fact.src}>Source: {fact.src}</span>}
          {old && <span>{fact.replaced_by ? "Replaced" : "Retired"} {fact.until && dayLabel(fact.until)}{fact.reason && fact.reason !== "replaced" ? `: ${fact.reason}` : ""}</span>}
        </div>
      </div>
      {!editing && !old && (
        <div className="fact-actions">
          {fact.review === "new" && (
            <button className="btn sm" disabled={busy} title={fact.flag ? "Keep it even though it looks like an instruction" : "Keep this fact"}
                    onClick={() => run(() => api.post(`/api/memory/facts/${fact.id}/approve`), "Approved")}><Check />{fact.flag ? "Keep anyway" : "Keep"}</button>
          )}
          <button className="btn sm ghost icon" title="Edit" onClick={() => setEditing(true)}><Pencil /></button>
          <button className="btn sm ghost icon" title={fact.review === "new" ? "Reject" : "Retire (moves to History)"} disabled={busy}
                  onClick={() => run(() => fact.review === "new" ? api.post(`/api/memory/facts/${fact.id}/reject`)
                    : api.post(`/api/memory/facts/${fact.id}/forget`, { reason: "retired by owner" }), fact.review === "new" ? "Rejected" : "Moved to History")}>
            {fact.review === "new" ? <X /> : <Undo2 />}
          </button>
        </div>
      )}
    </div>
  );
}
