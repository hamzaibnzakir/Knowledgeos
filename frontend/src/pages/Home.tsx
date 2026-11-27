import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { motion } from "motion/react";
import { ArrowRight, CalendarDays, Check, Inbox, Plus, Search } from "lucide-react";
import { api, noteHref, qs } from "../lib/api";
import { folderColor } from "../lib/colors";
import { useCreateNote, useNotes, useOpenDaily, useSystem, useVaultRefresh } from "../lib/hooks";
import type { AuditEvent, GraphData } from "../lib/types";
import Islands from "../components/Islands";
import { Cascade, CountUp, Headline, rise } from "../components/motion";
import { errorText, timeAgo, useToast } from "../components/ui";

const ACTION_WORDS: Record<string, string> = {
  "note.create": "Created", "note.update": "Edited", "note.edit": "Edited", "note.append": "Added to",
  "note.move": "Moved", "note.delete": "Deleted", "note.restore": "Restored", "note.relink": "Relinked",
  "memory.remember": "Saved a fact in", "memory.forget": "Retired a fact", "memory.approve": "Approved a fact",
  "memory.propose": "Proposed a change to", "memory.migrate": "Moved memories into Markdown",
  "memory.reject": "Rejected a fact", "memory.proposal": "Decided on a proposal", "memory.purge": "Deleted a fact",
  "note.revise": "Revised a fact in", "note.forget": "Retired a fact in", "note.review": "Reviewed", "note.remember": "Saved a fact in",
  "note.purge": "Deleted for good", "folder.create": "Created a folder", "attachment.upload": "Uploaded",
  "index.rebuild": "Rebuilt the search index", "index.reconcile": "Picked up outside edits",
  "auth.login": "Signed in", "backup.create": "Backed up the vault", "token.create": "Added an agent",
  "token.revoke": "Removed an agent", "oauth.consent": "Connected an agent",
};
const describe = (a: string) => ACTION_WORDS[a] ?? (a.split(".").pop() ?? a).replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());

function greeting() {
  const h = new Date().getHours();
  return h < 5 ? "Still up" : h < 12 ? "Good morning" : h < 17 ? "Good afternoon" : "Good evening";
}

function who(actor: string) {
  if (actor === "web") return "you";
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
  const graph = useQuery({
    queryKey: ["graph", "home"],
    queryFn: async () => {
      const g = await api.get<GraphData>("/api/graph");
      const keep = new Set(g.nodes.filter((n) => !/^(Memory|Agents|Archive)\//.test(n.id)).map((n) => n.id));
      return { nodes: g.nodes.filter((n) => keep.has(n.id)), edges: g.edges.filter((e) => keep.has(e.source) && keep.has(e.target)) };
    },
  });
  const activity = useQuery({ queryKey: ["audit", "home"], queryFn: () => api.get<AuditEvent[]>("/api/audit?limit=12") });

  const all = notes.data ?? [];
  // Home shows your notes; memory files and agent logs have their own pages.
  const isNote = (folder: string) => !/^(Memory|Agents|Archive)(\/|$)/.test(folder);
  const recent = all.filter((n) => isNote(n.folder)).slice(0, 6);
  const inboxAll = all.filter((n) => n.folder === "Inbox");
  const inbox = inboxAll.slice(0, 4);
  const s = system.data?.stats;
  const events = (activity.data ?? []).filter((e) => e.action !== "auth.login").slice(0, 5);

  const capture = async () => {
    if (!text.trim()) return;
    try {
      await api.post("/api/capture", { text });
      setText("");
      refresh();
      toast("Saved to Inbox");
    } catch (e) { toast(errorText(e), { kind: "error" }); }
  };
  const search = () => text.trim() && nav(`/search${qs({ q: text.trim() })}`);

  const name = system.data?.owner_name?.trim();
  const unreviewed = s?.unreviewed ?? 0;

  return (
    <div className="page">
      <Cascade className="bento">
        <motion.section variants={rise} className="tile b-hello">
          <div className="spread" style={{ alignItems: "flex-start" }}>
            <Headline className="hello-title" lines={[`${greeting()},`, name ? `${name}.` : "welcome back."]} />
            <div className="row hide-sm">
              <button className="btn ghost sm icon" title="Today's note" onClick={openDaily}><CalendarDays /></button>
              <button className="btn sm" onClick={() => createNote()}><Plus />New note</button>
            </div>
          </div>
          <p className="hello-sub">
            {s ? <>{s.notes.toLocaleString()} notes, {s.memories.toLocaleString()} shared facts, {inboxAll.length === 0 ? "and an empty inbox" : `${inboxAll.length} thing${inboxAll.length === 1 ? "" : "s"} in your inbox`}.</> : "Opening your vault…"}
          </p>
          <div className="capture">
            <textarea placeholder="Jot something down, or look something up…" value={text} onChange={(e) => setText(e.target.value)}
                      onKeyDown={(e) => { if ((e.metaKey || e.ctrlKey) && e.key === "Enter") { e.preventDefault(); capture(); } }} />
            <div className="actions">
              <span className="small muted">Saved notes land in Inbox.</span>
              <div className="row">
                <button className="btn" disabled={!text.trim()} onClick={search}><Search />Search</button>
                <button className="btn primary" disabled={!text.trim()} onClick={capture}><Inbox />Save to Inbox</button>
              </div>
            </div>
          </div>
        </motion.section>

        <motion.section variants={rise} className="tile b-map">
          <div className="tile-head"><h2>Your vault</h2><Link to="/graph">Open graph</Link></div>
          <p className="small muted" style={{ margin: 0 }}>Each island is a folder. Lines are links between notes.</p>
          <Islands data={graph.data} />
        </motion.section>

        <motion.div variants={rise} className="b-review" whileHover={{ y: -4 }} transition={{ type: "spring", stiffness: 400, damping: 20 }}>
          <Link to="/review" className={`tile review-tile ${unreviewed ? "" : "clear"}`} style={{ height: "100%" }}>
            <h2>To review</h2>
            <span className="go"><ArrowRight size={16} /></span>
            <div>
              {unreviewed ? <CountUp value={unreviewed} className="n" /> : <span className="n"><Check size={64} strokeWidth={1.5} /></span>}
              <p>{unreviewed ? "Facts and proposals from your agents" : "All clear. Nothing waiting."}</p>
            </div>
          </Link>
        </motion.div>

        <motion.section variants={rise} className="tile b-inbox">
          <div className="tile-head"><h2>Inbox</h2>{inboxAll.length > 0 && <span className="small muted">{inboxAll.length} to sort</span>}</div>
          <div className="inbox-list">
            {inbox.length === 0 ? <p className="muted" style={{ margin: "8px 0 0" }}>Clear. Captures land here until you file them.</p>
              : inbox.map((n) => <Link key={n.path} to={noteHref(n.path)}>{n.title.replace(/^\d{4}-\d{2}-\d{2}\s*/, "")}</Link>)}
          </div>
        </motion.section>

        <motion.section variants={rise} className="tile b-recent">
          <div className="tile-head"><h2>Recently edited</h2><Link to="/search">Browse all</Link></div>
          {notes.isLoading ? <p className="muted">Loading…</p>
            : recent.length === 0 ? <p className="muted">Nothing here yet. Write your first note, or drop Markdown files into the vault folder.</p>
            : <div className="recent-grid">
                {recent.map((n) => (
                  <Link key={n.path} to={noteHref(n.path)} className="recent-card">
                    <div className="top"><span className="t">{n.title}</span><span className="when">{timeAgo(n.updated_at)}</span></div>
                    <div className="s">{n.excerpt || "Empty note"}</div>
                    <div className="f" style={{ color: folderColor(n.folder) }}>{n.folder || "Vault"}</div>
                  </Link>
                ))}
              </div>}
        </motion.section>

        <motion.section variants={rise} className="tile b-changes">
          <div className="tile-head"><h2>What changed</h2><Link to="/activity">Full log</Link></div>
          {events.length === 0 ? <p className="muted">No changes yet.</p> : events.map((e) => {
            const path = typeof e.details.path === "string" ? e.details.path : typeof e.details.to === "string" ? e.details.to : null;
            const nm = path ? path.split("/").pop()!.replace(/\.md$/, "") : null;
            return (
              <div key={e.id} className="feed-item">
                <div>{describe(e.action)}{nm && <> {path && !e.action.endsWith("delete") && !e.action.endsWith("purge") ? <Link to={noteHref(path)}>{nm}</Link> : nm}</>}</div>
                <div className="when">{who(e.actor) === "you" ? "You" : who(e.actor)}, {timeAgo(e.created_at)}</div>
              </div>
            );
          })}
        </motion.section>
      </Cascade>
    </div>
  );
}
