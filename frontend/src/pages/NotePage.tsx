import { Fragment, lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Eye, History, MoreHorizontal, Network, PanelRight, Pencil, Star, Trash2, FolderInput } from "lucide-react";
import { api, ApiError, noteHref, qs } from "../lib/api";
import { renderMarkdown } from "../lib/markdown";
import { useVaultRefresh } from "../lib/hooks";
import type { Note, NoteContext, Version } from "../lib/types";
import { Empty, errorText, Modal, timeAgo, useDialog, useToast } from "../components/ui";

const Editor = lazy(() => import("../components/Editor"));

type SaveState = "saved" | "dirty" | "saving" | "error" | "conflict";

export default function NotePage() {
  const loc = useLocation();
  const path = decodeURIComponent(loc.pathname.replace(/^\/note\//, ""));
  const [params, setParams] = useSearchParams();
  const editing = params.get("edit") === "1";
  const nav = useNavigate();
  const qc = useQueryClient();
  const toast = useToast();
  const { prompt, confirm } = useDialog();
  const refresh = useVaultRefresh();

  const note = useQuery({ queryKey: ["note", path], queryFn: () => api.get<Note>(`/api/notes/read${qs({ path })}`), staleTime: 0 });
  const ctx = useQuery({ queryKey: ["note-context", path], queryFn: () => api.get<NoteContext>(`/api/notes/context${qs({ path })}`), enabled: note.isSuccess });

  const [text, setText] = useState("");
  const [state, setState] = useState<SaveState>("saved");
  const hashRef = useRef<string>("");
  const textRef = useRef("");
  const timer = useRef<number | undefined>(undefined);
  const [panel, setPanel] = useState(() => {
    if (window.innerWidth <= 1100) return false; // on small screens the panel is an overlay, opened on demand
    try { return localStorage.getItem("kos-panel") !== "0"; } catch { return true; }
  });
  const [versions, setVersions] = useState(false);
  const [menu, setMenu] = useState(false);

  // Load note into local state when the path changes or the server copy changes while clean.
  useEffect(() => {
    if (!note.data) return;
    if (state === "saved" || hashRef.current === "" || note.data.path !== path) {
      setText(note.data.content);
      textRef.current = note.data.content;
      hashRef.current = note.data.hash;
      setState("saved");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [note.data?.hash, path]);
  useEffect(() => { hashRef.current = ""; setState("saved"); }, [path]);

  const save = useCallback(async (force = false) => {
    window.clearTimeout(timer.current);
    if (!note.data) return;
    const content = textRef.current;
    setState("saving");
    try {
      const r = await api.put<{ hash: string }>("/api/notes", { path: note.data.path, content, expected_hash: force ? null : hashRef.current });
      hashRef.current = r.hash;
      setState(textRef.current === content ? "saved" : "dirty");
      qc.setQueryData(["note", path], (n: Note | undefined) => (n ? { ...n, content, hash: r.hash } : n));
      qc.invalidateQueries({ queryKey: ["note-context", path] });
      qc.invalidateQueries({ queryKey: ["notes"] });
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) setState("conflict");
      else { setState("error"); toast(`Save failed: ${errorText(e)}`, { kind: "error" }); }
    }
  }, [note.data, path, qc, toast]);

  const onChange = useCallback((v: string) => {
    textRef.current = v;
    setText(v);
    setState("dirty");
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => save(), 1200);
  }, [save]);

  useEffect(() => {
    const warn = (e: BeforeUnloadEvent) => { if (state === "dirty" || state === "saving") { e.preventDefault(); } };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [state]);
  // Flush pending edits when leaving the note (uses refs, so it never saves into the wrong note).
  const stateRef = useRef<SaveState>("saved");
  stateRef.current = state;
  const loadedPath = useRef("");
  if (note.data && note.data.path === path) loadedPath.current = note.data.path;
  useEffect(() => {
    return () => {
      window.clearTimeout(timer.current);
      if (stateRef.current === "dirty" && loadedPath.current) {
        api.put("/api/notes", { path: loadedPath.current, content: textRef.current, expected_hash: hashRef.current })
          .then(() => qc.invalidateQueries({ queryKey: ["notes"] }))
          .catch(() => toast("Your last edit could not be saved (the note changed elsewhere).", { kind: "error" }));
      }
    };
  }, [path, qc, toast]);

  const html = useMemo(() => renderMarkdown(text), [text]);
  const outgoing = ctx.data?.outgoing ?? [];

  const openTarget = async (target: string) => {
    const key = target.toLowerCase().replace(/\.md$/, "");
    const hit = outgoing.find((o) => o.target_raw.toLowerCase().replace(/\.md$/, "") === key && o.target_path);
    if (hit?.target_path) return nav(noteHref(hit.target_path));
    const quick = await api.get<{ path: string; title: string }[]>(`/api/quick${qs({ q: target, limit: 5 })}`);
    const exact = quick.find((n) => n.title.toLowerCase() === key || n.path.toLowerCase().replace(/\.md$/, "") === key);
    if (exact) return nav(noteHref(exact.path));
    if (await confirm(`Create “${target}”?`, { body: "This link points to a note that does not exist yet.", confirm: "Create note" })) {
      const folder = path.includes("/") ? path.slice(0, path.lastIndexOf("/")) : "Notes";
      const r = await api.post<{ path: string }>("/api/notes", { path: target.includes("/") ? target : `${folder}/${target}`, content: `# ${target.split("/").pop()}\n\n` });
      refresh();
      nav(noteHref(r.path) + "?edit=1");
    }
  };

  const onPreviewClick = (e: React.MouseEvent) => {
    const el = (e.target as HTMLElement).closest("a");
    if (!el) return;
    if (el.classList.contains("wikilink")) { e.preventDefault(); openTarget(el.dataset.target ?? ""); }
    else if (el.classList.contains("tag")) { e.preventDefault(); nav(`/search${qs({ tag: el.dataset.tag })}`); }
  };

  const toggleEdit = () => {
    if (editing && state === "dirty") save();
    setParams(editing ? {} : { edit: "1" }, { replace: true });
  };
  const togglePanel = () => setPanel((p) => { try { localStorage.setItem("kos-panel", p ? "0" : "1"); } catch { /* ignore */ } return !p; });

  const toggleFav = async () => {
    if (!note.data) return;
    await api.post("/api/notes/favorite", { path: note.data.path, favorite: !note.data.favorite });
    qc.invalidateQueries({ queryKey: ["note", path] });
    qc.invalidateQueries({ queryKey: ["notes"] });
  };

  const move = async () => {
    setMenu(false);
    if (!note.data) return;
    if (state === "dirty") await save();
    const target = await prompt("Rename or move", { label: "New path (links in other notes are updated)", value: note.data.path.replace(/\.md$/, "") });
    if (!target || target === note.data.path.replace(/\.md$/, "")) return;
    try {
      const r = await api.post<{ path: string; updated_links: number }>("/api/notes/move", { path: note.data.path, new_path: target });
      refresh();
      toast(r.updated_links ? `Moved; updated ${r.updated_links} link${r.updated_links > 1 ? "s" : ""}` : "Moved");
      nav(noteHref(r.path), { replace: true });
    } catch (e) { toast(errorText(e), { kind: "error" }); }
  };

  const remove = async () => {
    setMenu(false);
    if (!note.data) return;
    if (!(await confirm(`Delete “${note.data.title ?? path}”?`, { body: "It moves to the trash; you can restore it from Settings.", danger: true, confirm: "Delete" }))) return;
    const r = await api.del<{ trash_id: string }>(`/api/notes${qs({ path: note.data.path })}`);
    refresh();
    nav("/");
    toast("Moved to trash", { action: { label: "Undo", run: async () => { const x = await api.post<{ path: string }>(`/api/trash/${r.trash_id}/restore`); refresh(); nav(noteHref(x.path)); } } });
  };

  if (note.isLoading) return <div className="page"><Empty>Loading…</Empty></div>;
  if (note.isError) return <div className="page"><Empty>{(note.error as ApiError).status === 404 ? <>This note does not exist. <Link to="/">Go home</Link></> : errorText(note.error)}</Empty></div>;
  const n = note.data!;
  const folders = n.path.split("/").slice(0, -1);

  return (
    <div className={`note-layout ${panel ? "show-panel" : "no-panel"}`} style={{ flex: 1, minHeight: 0 }}>
      <div className="note-main">
        <div className="note-bar">
          <div className="crumbs grow">
            {folders.map((f, i) => <span key={i} className="hide-sm">{f} /</span>)}
            <b>{n.title ?? n.path}</b>
          </div>
          <span className={`save-state ${state === "dirty" ? "dirty" : state === "error" || state === "conflict" ? "error" : ""}`}>
            {state === "saved" ? (n.updated_at ? `Saved · ${timeAgo(n.updated_at)}` : "Saved") : state === "dirty" ? "Unsaved" : state === "saving" ? "Saving…" : state === "conflict" ? "Conflict" : "Save failed"}
          </span>
          <button className="btn ghost sm icon" title={n.favorite ? "Unfavorite" : "Favorite"} onClick={toggleFav}>
            <Star fill={n.favorite ? "currentColor" : "none"} />
          </button>
          <button className="btn sm" onClick={toggleEdit} title="Toggle edit / read">{editing ? <><Eye />Read</> : <><Pencil />Edit</>}</button>
          <button className="btn ghost sm icon" title="Details panel" onClick={togglePanel}><PanelRight /></button>
          <div style={{ position: "relative" }}>
            <button className="btn ghost sm icon" title="More" onClick={() => setMenu((m) => !m)}><MoreHorizontal /></button>
            {menu && (
              <div className="card" style={{ position: "absolute", right: 0, top: 30, zIndex: 20, padding: 4, minWidth: 180, boxShadow: "var(--shadow)" }} onMouseLeave={() => setMenu(false)}>
                <div className="nav" style={{ padding: 0 }}>
                  <button onClick={move}><FolderInput />Rename / move</button>
                  <button onClick={() => { setMenu(false); setVersions(true); }}><History />Version history</button>
                  <button onClick={() => nav(`/graph${qs({ focus: n.path })}`)}><Network />Open in graph</button>
                  <button onClick={remove} style={{ color: "var(--bad)" }}><Trash2 />Delete</button>
                </div>
              </div>
            )}
          </div>
        </div>
        {state === "conflict" && (
          <div className="row" style={{ padding: "8px 16px", background: "var(--bg-2)", borderBottom: "1px solid var(--line)" }}>
            <span className="grow small">This note changed somewhere else (another device or an agent) since you opened it.</span>
            <button className="btn sm" onClick={async () => { const fresh = await note.refetch(); if (fresh.data) { setText(fresh.data.content); textRef.current = fresh.data.content; hashRef.current = fresh.data.hash; setState("saved"); } }}>Load theirs</button>
            <button className="btn sm danger" onClick={() => save(true)}>Keep mine</button>
          </div>
        )}
        <div className="note-body">
          {editing ? (
            <Suspense fallback={<Empty>Loading editor…</Empty>}>
              <Editor value={text} onChange={onChange} onSave={() => save()} />
            </Suspense>
          ) : (
            <article className="prose" onClick={onPreviewClick} onDoubleClick={() => setParams({ edit: "1" }, { replace: true })}
                     dangerouslySetInnerHTML={{ __html: html || "<p class='muted'>Empty note — double-click to start writing.</p>" }} />
          )}
        </div>
      </div>
      {panel && <NoteSide note={n} ctx={ctx.data} onClose={togglePanel} />}
      {versions && <Versions path={n.path} onClose={() => setVersions(false)} onRestored={() => { setVersions(false); note.refetch(); }} />}
    </div>
  );
}

function NoteSide({ note, ctx, onClose }: { note: Note; ctx?: NoteContext; onClose: () => void }) {
  const props = Object.entries(note.properties ?? {});
  return (
    <aside className="note-side">
      <div className="spread" style={{ marginBottom: 8 }}>
        <span className="small muted">{note.word_count ?? 0} words</span>
        <button className="btn ghost sm hide-desktop" onClick={onClose}>Close</button>
      </div>
      {(note.tags?.length || props.length || note.aliases?.length) ? (
        <div className="side-block">
          <h4>Properties</h4>
          <dl className="props">
            {note.tags?.length ? <><dt>tags</dt><dd className="row wrap" style={{ gap: 4 }}>{note.tags.map((t) => <Link key={t} to={`/search${qs({ tag: t })}`} className="chip accent">#{t}</Link>)}</dd></> : null}
            {note.aliases?.length ? <><dt>aliases</dt><dd>{note.aliases.join(", ")}</dd></> : null}
            {props.map(([k, v]) => <Fragment key={k}><dt>{k}</dt><dd title={String(v)}>{typeof v === "object" ? JSON.stringify(v) : String(v)}</dd></Fragment>)}
          </dl>
        </div>
      ) : null}
      <div className="side-block">
        <h4>Backlinks <span>{ctx?.backlinks.length ?? ""}</span></h4>
        {ctx?.backlinks.length ? ctx.backlinks.map((b) => <Link key={b.path} className="side-link" to={noteHref(b.path)}>{b.title}</Link>)
          : <div className="muted small">No notes link here yet.</div>}
      </div>
      <div className="side-block">
        <h4>Links <span>{ctx?.outgoing.length ?? ""}</span></h4>
        {ctx?.outgoing.length ? ctx.outgoing.map((o) => o.target_path
          ? <Link key={o.target_raw + o.kind} className="side-link" to={noteHref(o.target_path)}>{o.target_raw}</Link>
          : <span key={o.target_raw + o.kind} className="side-link missing" title="Not created yet">{o.target_raw}</span>)
          : <div className="muted small">Link notes with [[Title]].</div>}
      </div>
      <div className="side-block">
        <h4>Related</h4>
        {ctx?.related.length ? ctx.related.map((r) => (
          <Link key={r.path} className="side-link" to={noteHref(r.path)} title={r.reasons.join(", ")}>
            {r.title} <small>· {r.reasons.slice(0, 2).join(", ")}</small>
          </Link>)) : <div className="muted small">Nothing related found yet.</div>}
      </div>
      {ctx?.unlinked_mentions.length ? (
        <div className="side-block">
          <h4>Unlinked mentions</h4>
          {ctx.unlinked_mentions.map((m) => <Link key={m.path} className="side-link" to={noteHref(m.path)}>{m.path.replace(/\.md$/, "")}</Link>)}
        </div>
      ) : null}
      <div className="side-block small muted">
        Created {timeAgo(note.created_at)} · {ctx?.versions ?? 0} earlier version{ctx?.versions === 1 ? "" : "s"}
        <div style={{ wordBreak: "break-all", marginTop: 4 }}>{note.path}</div>
      </div>
    </aside>
  );
}

function Versions({ path, onClose, onRestored }: { path: string; onClose: () => void; onRestored: () => void }) {
  const list = useQuery({ queryKey: ["versions", path], queryFn: () => api.get<Version[]>(`/api/notes/versions${qs({ path })}`) });
  const [sel, setSel] = useState<number | null>(null);
  const v = useQuery({ queryKey: ["version", sel], queryFn: () => api.get<Version & { content: string }>(`/api/versions/${sel}`), enabled: sel !== null });
  const toast = useToast();
  const restore = async () => {
    if (sel === null) return;
    await api.post(`/api/versions/${sel}/restore`);
    toast("Version restored (the current text was kept as a version too)");
    onRestored();
  };
  return (
    <Modal title="Version history" onClose={onClose} wide footer={<><button className="btn" onClick={onClose}>Close</button><button className="btn primary" disabled={sel === null} onClick={restore}>Restore this version</button></>}>
      {list.data?.length === 0 && <Empty>No earlier versions. A version is saved every time the note changes.</Empty>}
      <div className="row" style={{ alignItems: "stretch", gap: 12 }}>
        <div className="list card" style={{ width: 220, flex: "none", maxHeight: 420, overflow: "auto" }}>
          {list.data?.map((x) => (
            <button key={x.id} className="list-item" style={{ textAlign: "left", border: 0, background: sel === x.id ? "var(--accent-soft)" : "none" }} onClick={() => setSel(x.id)}>
              <div className="t small">{new Date(x.created_at).toLocaleString()}</div>
              <div className="s">{x.reason} · {x.actor}</div>
            </button>
          ))}
        </div>
        <pre className="grow card card-pad" style={{ margin: 0, maxHeight: 420, overflow: "auto", whiteSpace: "pre-wrap", fontSize: 12 }}>
          {sel === null ? "Select a version to preview it." : v.data?.content ?? "Loading…"}
        </pre>
      </div>
    </Modal>
  );
}
