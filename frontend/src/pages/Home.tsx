import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { CalendarDays, FilePlus, Inbox, MessageSquare, Send } from "lucide-react";
import { api, noteHref, qs } from "../lib/api";
import { useCreateNote, useNotes, useOpenDaily, useSystem, useVaultRefresh } from "../lib/hooks";
import type { AuditEvent } from "../lib/types";
import { Empty, errorText, timeAgo, useToast } from "../components/ui";

export default function Home() {
  const notes = useNotes();
  const system = useSystem();
  const createNote = useCreateNote();
  const openDaily = useOpenDaily();
  const refresh = useVaultRefresh();
  const toast = useToast();
  const nav = useNavigate();
  const [capture, setCapture] = useState("");
  const [ask, setAsk] = useState("");
  const activity = useQuery({ queryKey: ["audit", "home"], queryFn: () => api.get<AuditEvent[]>("/api/audit?limit=8") });
  const inbox = (notes.data ?? []).filter((n) => n.folder === "Inbox").slice(0, 5);
  const recent = (notes.data ?? []).slice(0, 10);
  const s = system.data?.stats;

  const saveCapture = async () => {
    if (!capture.trim()) return;
    try {
      await api.post("/api/capture", { text: capture });
      setCapture("");
      refresh();
      toast("Captured to Inbox");
    } catch (e) { toast(errorText(e), { kind: "error" }); }
  };

  return (
    <div className="page">
      <div className="page-inner">
        <div className="spread" style={{ marginBottom: 20 }}>
          <div>
            <h1 className="title">Home</h1>
            <div className="muted">{s ? `${s.notes} notes · ${s.links} links · ${s.memories} memories` : "…"}</div>
          </div>
          <div className="row">
            <button className="btn" onClick={openDaily}><CalendarDays />Today</button>
            <button className="btn primary" onClick={() => createNote()}><FilePlus />New note</button>
          </div>
        </div>

        <div className="grid cols-2">
          <div className="card card-pad stack">
            <div className="row"><Inbox size={16} /><b>Quick capture</b></div>
            <textarea className="textarea" rows={3} placeholder="An idea, a link, a thing to remember later…" value={capture}
                      onChange={(e) => setCapture(e.target.value)}
                      onKeyDown={(e) => { if ((e.metaKey || e.ctrlKey) && e.key === "Enter") saveCapture(); }} />
            <div className="spread"><span className="muted small">Saved to Inbox/ · ⌘↵</span><button className="btn sm" disabled={!capture.trim()} onClick={saveCapture}>Capture</button></div>
          </div>
          <div className="card card-pad stack">
            <div className="row"><MessageSquare size={16} /><b>Ask your knowledge</b></div>
            <form className="stack" onSubmit={(e) => { e.preventDefault(); if (ask.trim()) nav(`/chat${qs({ q: ask.trim() })}`); }}>
              <textarea className="textarea" rows={3} placeholder="What did I learn about scaling winning products?" value={ask} onChange={(e) => setAsk(e.target.value)}
                        onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); if (ask.trim()) nav(`/chat${qs({ q: ask.trim() })}`); } }} />
              <div className="spread">
                <span className="muted small">{system.data?.ai.configured ? `Answers cite your notes · ${system.data.ai.agent_model}` : "AI not configured — search still works"}</span>
                <button className="btn sm" disabled={!ask.trim()}><Send />Ask</button>
              </div>
            </form>
          </div>
        </div>

        <h2 className="section">Recently edited</h2>
        <div className="card list">
          {notes.isLoading ? <Empty>Loading…</Empty> : recent.length === 0 ? <Empty>No notes yet. Create one, capture an idea, or drop Markdown files into the vault folder.</Empty> :
            recent.map((n) => (
              <Link key={n.path} to={noteHref(n.path)} className="list-item">
                <div className="spread"><span className="t">{n.title}</span><span className="muted small">{timeAgo(n.updated_at)}</span></div>
                <div className="s">{n.folder && <span className="muted">{n.folder} · </span>}{n.excerpt}</div>
              </Link>
            ))}
        </div>

        <div className="grid cols-2" style={{ marginTop: 12 }}>
          <div>
            <h2 className="section">Inbox</h2>
            <div className="card list">
              {inbox.length === 0 ? <Empty>Inbox is clear.</Empty> : inbox.map((n) => (
                <Link key={n.path} to={noteHref(n.path)} className="list-item"><div className="t">{n.title}</div><div className="s">{n.excerpt}</div></Link>
              ))}
            </div>
          </div>
          <div>
            <h2 className="section">Latest activity</h2>
            <div className="card list">
              {(activity.data ?? []).length === 0 ? <Empty>Nothing yet.</Empty> : activity.data!.map((e) => (
                <div key={e.id} className="list-item">
                  <div className="spread"><span className="small"><code>{e.action}</code> <span className="muted">by {e.actor}</span></span><span className="muted small">{timeAgo(e.created_at)}</span></div>
                  {typeof e.details.path === "string" && <div className="s">{e.details.path}</div>}
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
