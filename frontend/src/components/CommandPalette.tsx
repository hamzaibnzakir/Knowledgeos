import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Activity, Bot, Brain, CalendarDays, FilePlus, FileText, Inbox, Moon, Network, RefreshCw, Search, Settings } from "lucide-react";
import { api, noteHref, qs } from "../lib/api";
import { useCreateNote, useOpenDaily } from "../lib/hooks";
import { useDebounced, useTheme, useToast } from "./ui";

interface Item { id: string; label: string; hint?: string; icon: React.ReactNode; run: () => void }

export default function CommandPalette({ onClose }: { onClose: () => void }) {
  const [q, setQ] = useState("");
  const [sel, setSel] = useState(0);
  const dq = useDebounced(q, 120);
  const nav = useNavigate();
  const createNote = useCreateNote();
  const openDaily = useOpenDaily();
  const [theme, setTheme] = useTheme();
  const toast = useToast();
  const listRef = useRef<HTMLDivElement>(null);
  const found = useQuery({ queryKey: ["quick", dq], queryFn: () => api.get<{ path: string; title: string }[]>(`/api/quick${qs({ q: dq, limit: 12 })}`) });

  const items = useMemo<Item[]>(() => {
    const go = (to: string) => () => { nav(to); onClose(); };
    const commands: Item[] = [
      { id: "new", label: q.trim() ? `Create note “${q.trim()}”` : "New note", icon: <FilePlus />, hint: "Alt+N", run: () => { onClose(); createNote("Notes", q.trim()); } },
      { id: "daily", label: "Open today's note", icon: <CalendarDays />, run: () => { onClose(); openDaily(); } },
      { id: "search", label: q.trim() ? `Search everything for “${q.trim()}”` : "Search", icon: <Search />, hint: "⌘⇧F", run: go(`/search${qs({ q: q.trim() })}`) },
      { id: "review", label: "Review what agents saved", icon: <Inbox />, run: go("/review") },
      { id: "graph", label: "Graph", icon: <Network />, run: go("/graph") },
      { id: "memory", label: "Memory", icon: <Brain />, run: go("/memory") },
      { id: "brief", label: "See what agents are told (brief)", icon: <Bot />, run: go("/memory?view=brief") },
      { id: "agents", label: "Agents & MCP", icon: <Bot />, run: go("/agents") },
      { id: "activity", label: "Activity log", icon: <Activity />, run: go("/activity") },
      { id: "settings", label: "Settings", icon: <Settings />, run: go("/settings") },
      { id: "theme", label: `Theme: ${theme} → ${theme === "light" ? "dark" : theme === "dark" ? "system" : "light"}`, icon: <Moon />, run: () => setTheme(theme === "light" ? "dark" : theme === "dark" ? "system" : "light") },
      { id: "reindex", label: "Re-scan vault for outside changes", icon: <RefreshCw />, run: async () => { onClose(); const r = await api.post<Record<string, number>>("/api/reindex", { full: false }); toast(`Indexed ${r.indexed}, removed ${r.removed}`); } },
    ];
    const notes: Item[] = (found.data ?? []).map((n) => ({ id: n.path, label: n.title, hint: n.path, icon: <FileText />, run: go(noteHref(n.path)) }));
    const needle = q.trim().toLowerCase();
    const cmds = needle ? commands.filter((c) => ["new", "search"].includes(c.id) || c.label.toLowerCase().includes(needle)) : commands;
    return needle ? [...notes, ...cmds] : [...cmds.slice(0, 4), ...notes, ...cmds.slice(4)];
  }, [found.data, q, nav, onClose, createNote, openDaily, theme, setTheme, toast]);

  useEffect(() => setSel(0), [dq]);
  useEffect(() => { listRef.current?.querySelector(".on")?.scrollIntoView({ block: "nearest" }); }, [sel]);

  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown") { e.preventDefault(); setSel((s) => Math.min(items.length - 1, s + 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setSel((s) => Math.max(0, s - 1)); }
    else if (e.key === "Enter") { e.preventDefault(); items[sel]?.run(); }
    else if (e.key === "Escape") onClose();
  };

  return (
    <div className="overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal" role="dialog" aria-label="Command palette">
        <input className="palette-input" autoFocus placeholder="Jump to a note, or type a command…" value={q}
               onChange={(e) => setQ(e.target.value)} onKeyDown={onKey} />
        <div className="palette-list" ref={listRef}>
          {items.map((it, i) => (
            <div key={it.id} className={`palette-item ${i === sel ? "on" : ""}`} onMouseEnter={() => setSel(i)} onClick={it.run}>
              {it.icon}<span>{it.label}</span>{it.hint && <span className="hint">{it.hint}</span>}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
