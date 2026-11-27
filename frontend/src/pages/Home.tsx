import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { CalendarDays, Inbox, Plus, Sparkles } from "lucide-react";
import { api, noteHref, qs } from "../lib/api";
import { folderColor } from "../lib/colors";
import { useCreateNote, useNotes, useOpenDaily, useSystem, useVaultRefresh } from "../lib/hooks";
import type { AuditEvent, GraphData } from "../lib/types";
import Constellation from "../components/Constellation";
import { errorText, timeAgo, useToast } from "../components/ui";

const ACTION_WORDS: Record<string, string> = {
  "note.create": "Created", "note.update": "Edited", "note.edit": "Edited", "note.append": "Added to",
  "note.move": "Moved", "note.delete": "Deleted", "note.restore": "Restored", "note.relink": "Relinked",
  "memory.create": "Remembered a fact", "memory.forget": "Forgot a fact", "chat.answer": "Answered a question",
  "auth.login": "Signed in", "backup.create": "Backed up the vault", "token.create": "Added an agent",
};

function greeting() {
  const h = new Date().getHours();
  return h < 5 ? "Still up" : h < 12 ? "Good morning" : h < 17 ? "Good afternoon" : "Good evening";
}

function who(actor: string) {
  if (actor === "web") return "you";
  if (actor === "agent") return "assistant";
  return actor.replace(/^mcp:(agent:)?/, "").replace(/-[0-9a-f]{6}$/, "");
}

export default function Home() {
  const notes = useNotes();
  const system = useSystem();
  const createNote = useCreateNote();
  const openDaily = useOpenDaily();
  const refresh = useVaultRefresh();
  const toast = useToast();
  const nav = useNavigate();
  const [text, setText] = useState("");
  const graph = useQuery({ queryKey: ["graph", "home"], queryFn: () => api.get<GraphData>("/api/graph") });
  const activity = useQuery({ queryKey: ["audit", "home"], queryFn: () => api.get<AuditEvent[]>("/api/audit?limit=12") });

  const all = notes.data ?? [];
  const recent = all.slice(0, 8);
  const inbox = all.filter((n) => n.folder === "Inbox").slice(0, 5);
  const s = system.data?.stats;
  const events = (activity.data ?? []).filter((e) => e.action !== "auth.login").slice(0, 7);

  const capture = async () => {
    if (!text.trim()) return;
    try {
      await api.post("/api/capture", { text });
      setText("");
      refresh();
      toast("Saved to Inbox");
    } catch (e) { toast(errorText(e), { kind: "error" }); }
  };
  const ask = () => text.trim() && nav(`/chat${qs({ q: text.trim() })}`);

  return (
    <div className="page">
      <div className="home">
        <header className="home-head">
          <div>
            <h1>{greeting()}.</h1>
            <p>
              {s ? <>{s.notes.toLocaleString()} notes, {s.links.toLocaleString()} links between them, {s.memories} things remembered.</> : "Loading your vault…"}
            </p>
          </div>
          <div className="row">
            <button className="btn" onClick={openDaily}><CalendarDays />Today's note</button>
            <button className="btn primary" onClick={() => createNote()}><Plus />New note</button>
          </div>
        </header>

        <Constellation data={graph.data} recent={recent.map((n) => n.path)} />

        <div className="composer-home">
          <textarea placeholder="Jot something down, or ask what you know…" value={text} onChange={(e) => setText(e.target.value)}
                    onKeyDown={(e) => {
                      if ((e.metaKey || e.ctrlKey) && e.key === "Enter") { e.preventDefault(); capture(); }
                      else if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing && text.trim().endsWith("?")) { e.preventDefault(); ask(); }
                    }} />
          <div className="actions">
            <span className="small muted">
              {system.data?.ai.configured ? "Questions are answered from your notes, with sources." : "Ask shows matching passages until an AI key is set."}
            </span>
            <div className="row">
              <button className="btn sm" disabled={!text.trim()} onClick={capture}><Inbox />Save to Inbox</button>
              <button className="btn sm primary" disabled={!text.trim()} onClick={ask}><Sparkles />Ask</button>
            </div>
          </div>
        </div>

        <div className="home-cols">
          <section>
            <div className="col-head"><h2>Recently edited</h2><Link to="/search">Browse all</Link></div>
            <div className="rail-list">
              {notes.isLoading ? <div className="muted" style={{ padding: "12px 0" }}>Loading…</div>
                : recent.length === 0 ? <div className="muted" style={{ padding: "12px 0" }}>Nothing here yet. Write your first note, or drop Markdown files into the vault folder.</div>
                : recent.map((n) => (
                  <Link key={n.path} to={noteHref(n.path)} className="rail-item">
                    <span className="dot" style={{ background: folderColor(n.folder) }} title={n.folder || "Vault root"} />
                    <span style={{ minWidth: 0 }}>
                      <div className="t">{n.title}</div>
                      <div className="s">{n.excerpt || "Empty note"}</div>
                    </span>
                    <span className="when">{timeAgo(n.updated_at)}</span>
                  </Link>
                ))}
            </div>
          </section>

          <aside className="stack" style={{ gap: 28 }}>
            <section>
              <div className="col-head"><h2>Inbox</h2>{inbox.length > 0 && <span className="small muted">{inbox.length} to sort</span>}</div>
              {inbox.length === 0 ? <div className="feed-item muted">Clear. Captures land here until you file them.</div>
                : inbox.map((n) => (
                  <Link key={n.path} to={noteHref(n.path)} className="feed-item" style={{ display: "block", color: "var(--ink)" }}>{n.title}</Link>
                ))}
            </section>
            <section>
              <div className="col-head"><h2>What changed</h2><Link to="/activity">Full log</Link></div>
              {events.length === 0 ? <div className="feed-item muted">No changes yet.</div> : events.map((e) => {
                const path = typeof e.details.path === "string" ? e.details.path : typeof e.details.to === "string" ? e.details.to : null;
                const name = path ? path.split("/").pop()!.replace(/\.md$/, "") : null;
                return (
                  <div key={e.id} className="feed-item">
                    <div><b>{ACTION_WORDS[e.action] ?? e.action}</b>{name && <> {path && !e.action.endsWith("delete") ? <Link to={noteHref(path)}>{name}</Link> : name}</>}</div>
                    <div className="when">by {who(e.actor)}, {timeAgo(e.created_at)}</div>
                  </div>
                );
              })}
            </section>
          </aside>
        </div>
      </div>
    </div>
  );
}
