import { useMemo, useState } from "react";
import { Link, NavLink, useLocation } from "react-router-dom";
import {
  Activity, Bot, Brain, CalendarDays, ChevronDown, ChevronRight, FilePlus, FileText, Folder, FolderPlus, Home,
  Inbox, Network, Search, Settings, Star, X,
} from "lucide-react";
import { api, noteHref } from "../lib/api";
import { useCreateNote, useFolders, useNotes, useOpenDaily, useSystem, useVaultRefresh } from "../lib/hooks";
import type { Folder as FolderT, NoteSummary } from "../lib/types";
import { errorText, useDialog, useToast } from "./ui";

interface TreeNode { name: string; path: string; folders: Map<string, TreeNode>; notes: NoteSummary[] }

function buildTree(notes: NoteSummary[], folders?: FolderT): TreeNode {
  const root: TreeNode = { name: "", path: "", folders: new Map(), notes: [] };
  const ensure = (path: string) => {
    let node = root;
    if (!path) return node;
    let acc = "";
    for (const part of path.split("/")) {
      acc = acc ? `${acc}/${part}` : part;
      if (!node.folders.has(part)) node.folders.set(part, { name: part, path: acc, folders: new Map(), notes: [] });
      node = node.folders.get(part)!;
    }
    return node;
  };
  const walk = (f: FolderT) => { if (f.path) ensure(f.path); f.children.forEach(walk); };
  if (folders) walk(folders);
  for (const n of notes) ensure(n.folder).notes.push(n);
  return root;
}

function loadOpen(): Record<string, boolean> {
  try { return JSON.parse(localStorage.getItem("kos-tree") ?? "{}"); } catch { return {}; }
}

export default function Sidebar({ onPalette, onClose }: { onPalette: () => void; onClose: () => void }) {
  const notes = useNotes();
  const folders = useFolders();
  const system = useSystem();
  const createNote = useCreateNote();
  const openDaily = useOpenDaily();
  const refresh = useVaultRefresh();
  const toast = useToast();
  const { prompt } = useDialog();
  const loc = useLocation();
  const [open, setOpen] = useState<Record<string, boolean>>(loadOpen);
  const tree = useMemo(() => buildTree(notes.data ?? [], folders.data), [notes.data, folders.data]);
  const favorites = (notes.data ?? []).filter((n) => n.favorite);
  const currentPath = loc.pathname.startsWith("/note/") ? decodeURIComponent(loc.pathname.slice(6)) : "";

  const toggle = (p: string) => setOpen((o) => {
    const next = { ...o, [p]: !o[p] };
    try { localStorage.setItem("kos-tree", JSON.stringify(next)); } catch { /* ignore */ }
    return next;
  });

  const newFolder = async (parent = "") => {
    const name = await prompt("New folder", { label: parent ? `Inside ${parent}` : "Folder name" });
    if (!name) return;
    try {
      await api.post("/api/folders", { path: parent ? `${parent}/${name}` : name });
      refresh();
    } catch (e) { toast(errorText(e), { kind: "error" }); }
  };

  const renderFolder = (node: TreeNode, depth: number) => {
    const sortedFolders = [...node.folders.values()].sort((a, b) => a.name.localeCompare(b.name));
    const sortedNotes = [...node.notes].sort((a, b) => a.title.localeCompare(b.title));
    return (
      <>
        {sortedFolders.map((f) => {
          const isOpen = open[f.path] ?? false;
          const count = countNotes(f);
          return (
            <div key={f.path}>
              <div className="tree-row" style={{ paddingLeft: 8 + depth * 12 }} onClick={() => toggle(f.path)}
                   role="treeitem" aria-expanded={isOpen}>
                {isOpen ? <ChevronDown /> : <ChevronRight />}
                <Folder />
                <span className="name grow">{f.name}</span>
                <span className="muted small">{count || ""}</span>
                <button className="btn ghost sm icon" title={`New note in ${f.path}`}
                        onClick={(e) => { e.stopPropagation(); createNote(f.path); }}><FilePlus /></button>
              </div>
              {isOpen && renderFolder(f, depth + 1)}
            </div>
          );
        })}
        {sortedNotes.map((n) => (
          <Link key={n.path} to={noteHref(n.path)} className={`tree-row ${n.path === currentPath ? "active" : ""}`}
                style={{ paddingLeft: 26 + depth * 12 }} title={n.path}>
            <FileText /><span className="name">{n.title}</span>
          </Link>
        ))}
      </>
    );
  };

  return (
    <aside className="sidebar">
      <div className="brand">
        <div className="brand-mark"><Network size={15} /></div>
        <span className="grow">Knowledge OS</span>
        <button className="btn ghost sm icon hide-desktop" aria-label="Close menu" onClick={onClose}><X /></button>
      </div>
      <button className="side-search" onClick={onPalette}>
        <Search size={14} /><span>Search or jump…</span><kbd>⌘K</kbd>
      </button>
      <nav className="nav">
        <NavLink to="/" end><Home />Home</NavLink>
        <NavLink to="/search"><Search />Search</NavLink>
        <NavLink to="/memory"><Brain />Memory</NavLink>
        <NavLink to="/review"><Inbox />Review{!!system.data?.stats.unreviewed && <span className="badge">{system.data.stats.unreviewed}</span>}</NavLink>
        <NavLink to="/graph"><Network />Graph</NavLink>
        <NavLink to="/agents"><Bot />Agents &amp; MCP</NavLink>
        <NavLink to="/activity"><Activity />Activity</NavLink>
        <button onClick={openDaily}><CalendarDays />Today's note</button>
      </nav>

      {favorites.length > 0 && (
        <>
          <div className="side-section">Favorites</div>
          <div style={{ padding: "0 8px" }}>
            {favorites.map((n) => (
              <Link key={n.path} to={noteHref(n.path)} className={`tree-row ${n.path === currentPath ? "active" : ""}`}>
                <Star /><span className="name">{n.title}</span>
              </Link>
            ))}
          </div>
        </>
      )}

      <div className="side-section">
        <span>Vault</span>
        <span className="row" style={{ gap: 2 }}>
          <button title="New note (Alt+N)" onClick={() => createNote()}><FilePlus size={14} /></button>
          <button title="New folder" onClick={() => newFolder()}><FolderPlus size={14} /></button>
        </span>
      </div>
      <div className="tree" role="tree">
        {notes.isLoading ? <div className="muted small" style={{ padding: 8 }}>Loading…</div> : renderFolder(tree, 0)}
      </div>
      <div className="side-foot">
        <NavLink to="/settings" className="btn ghost sm grow" style={{ justifyContent: "flex-start" }}><Settings />Settings</NavLink>
      </div>
    </aside>
  );
}

function countNotes(n: TreeNode): number {
  let c = n.notes.length;
  n.folders.forEach((f) => { c += countNotes(f); });
  return c;
}
