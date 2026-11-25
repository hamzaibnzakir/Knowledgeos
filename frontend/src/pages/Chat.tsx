import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Brain, Check, FileText, Link2, List, Paperclip, Plus, Send, Trash2, X } from "lucide-react";
import { api, noteHref, qs } from "../lib/api";
import { renderChatMarkdown } from "../lib/markdown";
import { useSystem, useVaultRefresh } from "../lib/hooks";
import type { Action, Chat, Message } from "../lib/types";
import { bytes, Empty, errorText, Modal, timeAgo, useDialog, useToast } from "../components/ui";

interface Candidate { content: string; category: string; confidence: number; duplicate_of: number | null; keep?: boolean }
interface ChatAttachment { id: string; filename: string; size: number | null; note_path: string | null; chars: number }

export default function ChatPage() {
  const { chatId } = useParams();
  const [params, setParams] = useSearchParams();
  const nav = useNavigate();
  const qc = useQueryClient();
  const toast = useToast();
  const { prompt, confirm } = useDialog();
  const refresh = useVaultRefresh();
  const system = useSystem();
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState<string | null>(null);
  const [showList, setShowList] = useState(false);
  const [candidates, setCandidates] = useState<Candidate[] | null>(null);
  const scroller = useRef<HTMLDivElement>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const started = useRef(false);

  const chats = useQuery({ queryKey: ["chats"], queryFn: () => api.get<Chat[]>("/api/chats") });
  const messages = useQuery({ queryKey: ["messages", chatId], queryFn: () => api.get<Message[]>(`/api/chats/${chatId}/messages`), enabled: !!chatId });
  const atts = useQuery({ queryKey: ["chat-attachments", chatId], queryFn: () => api.get<ChatAttachment[]>(`/api/chats/${chatId}/attachments`), enabled: !!chatId });

  useEffect(() => { scroller.current?.scrollTo({ top: scroller.current.scrollHeight, behavior: "smooth" }); }, [messages.data, sending]);

  const send = async (text: string, targetChat?: string) => {
    const message = text.trim();
    if (!message || sending) return;
    let id = targetChat ?? chatId;
    try {
      if (!id) {
        const c = await api.post<Chat>("/api/chats", {});
        id = c.id;
        nav(`/chat/${id}`, { replace: true });
      }
      setSending(message);
      setDraft("");
      await api.post(`/api/chats/${id}/messages`, { message });
      await qc.invalidateQueries({ queryKey: ["messages", id] });
      qc.invalidateQueries({ queryKey: ["chats"] });
      refresh();
    } catch (e) {
      toast(errorText(e), { kind: "error" });
      setDraft(message);
    } finally {
      setSending(null);
    }
  };

  // Arriving with ?q=… (from Home or the palette) starts a new chat.
  useEffect(() => {
    const q = params.get("q");
    if (q && !started.current) {
      started.current = true;
      setParams({}, { replace: true });
      send(q, undefined);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const newChat = async () => {
    const c = await api.post<Chat>("/api/chats", {});
    qc.invalidateQueries({ queryKey: ["chats"] });
    nav(`/chat/${c.id}`);
    setShowList(false);
  };

  const deleteChat = async () => {
    if (!chatId || !(await confirm("Delete this conversation?", { body: "Messages and staged attachments are removed. Notes it created stay.", danger: true, confirm: "Delete" }))) return;
    await api.del(`/api/chats/${chatId}`);
    qc.invalidateQueries({ queryKey: ["chats"] });
    nav("/chat");
  };

  const upload = async (file: File) => {
    let id = chatId;
    if (!id) { const c = await api.post<Chat>("/api/chats", {}); id = c.id; nav(`/chat/${id}`, { replace: true }); }
    const fd = new FormData();
    fd.append("file", file);
    fd.append("chat_id", id!);
    try {
      const r = await api.post<{ filename: string; extracted_chars: number; extraction: string }>("/api/attachments", fd);
      toast(r.extracted_chars ? `Attached ${r.filename} — the assistant can read it now` : `Attached ${r.filename} (${r.extraction}; no text extracted)`);
      qc.invalidateQueries({ queryKey: ["chat-attachments", id] });
    } catch (e) { toast(errorText(e), { kind: "error" }); }
  };

  const addLink = async () => {
    const url = await prompt("Import a web page", { label: "URL — saved to Resources/ and usable in this chat", placeholder: "https://…" });
    if (!url) return;
    try {
      const r = await api.post<{ path: string; title: string }>("/api/ingest-link", { url, chat_id: chatId ?? null });
      refresh();
      qc.invalidateQueries({ queryKey: ["chat-attachments", chatId] });
      toast(`Saved “${r.title}”`, { action: { label: "Open", run: () => nav(noteHref(r.path)) } });
    } catch (e) { toast(errorText(e), { kind: "error" }); }
  };

  const extract = async () => {
    if (!chatId) return;
    try {
      const r = await api.post<{ candidates: Candidate[] }>(`/api/chats/${chatId}/extract-memories`);
      if (!r.candidates.length) return toast("No durable facts about you found in this chat");
      setCandidates(r.candidates.map((c) => ({ ...c, keep: !c.duplicate_of })));
    } catch (e) { toast(errorText(e), { kind: "error" }); }
  };

  const saveCandidates = async () => {
    const items = (candidates ?? []).filter((c) => c.keep).map(({ content, category, confidence }) => ({ content, category, confidence }));
    if (items.length) await api.post("/api/memories/bulk", { items });
    toast(`Saved ${items.length} memor${items.length === 1 ? "y" : "ies"}`);
    setCandidates(null);
    qc.invalidateQueries({ queryKey: ["memories"] });
  };

  const decide = async (a: Action, approve: boolean) => {
    if (!a.pending_id) return;
    try {
      await api.post(`/api/pending/${a.pending_id}`, { approve });
      toast(approve ? "Done" : "Rejected");
      refresh();
      qc.invalidateQueries({ queryKey: ["pending"] });
    } catch (e) { toast(errorText(e), { kind: "error" }); }
  };
  const pending = useQuery({ queryKey: ["pending", chatId], queryFn: () => api.get<{ id: string }[]>(`/api/pending${qs({ chat_id: chatId })}`), enabled: !!chatId });
  const openIds = new Set((pending.data ?? []).map((p) => p.id));

  const onBubbleClick = (e: React.MouseEvent, m: Message) => {
    const el = e.target as HTMLElement;
    const a = el.closest("a");
    if (a?.classList.contains("wikilink")) {
      e.preventDefault();
      const t = (a.dataset.target ?? "").toLowerCase();
      const src = m.meta.sources?.find((s) => s.title?.toLowerCase() === t || s.path?.toLowerCase().replace(/\.md$/, "") === t);
      if (src?.path) nav(noteHref(src.path));
      else api.get<{ path: string; title: string }[]>(`/api/quick${qs({ q: a.dataset.target, limit: 1 })}`).then((r) => r[0] && nav(noteHref(r[0].path)));
    } else if (el.classList.contains("cite")) {
      const src = m.meta.sources?.find((s) => String(s.n) === el.dataset.n);
      if (src?.path) nav(noteHref(src.path));
    }
  };

  return (
    <div className={`chat-layout ${showList ? "show-list" : ""}`} style={{ flex: 1, minHeight: 0 }}>
      <aside className="chat-list">
        <div className="spread" style={{ padding: "0 4px 8px" }}>
          <b className="small muted">CONVERSATIONS</b>
          <div className="row"><button className="btn sm" onClick={newChat}><Plus />New</button>
            <button className="btn ghost sm icon hide-desktop" onClick={() => setShowList(false)}><X /></button></div>
        </div>
        {chats.data?.map((c) => <Link key={c.id} to={`/chat/${c.id}`} className={c.id === chatId ? "active" : ""} onClick={() => setShowList(false)}>{c.title}<div className="muted small">{timeAgo(c.updated_at)}</div></Link>)}
        {chats.data?.length === 0 && <div className="muted small" style={{ padding: 8 }}>No conversations yet.</div>}
      </aside>
      <section className="chat-main">
        <div className="note-bar">
          <button className="btn ghost sm icon hide-desktop" onClick={() => setShowList(true)}><List /></button>
          <b className="grow" style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{chats.data?.find((c) => c.id === chatId)?.title ?? "New conversation"}</b>
          {chatId && <button className="btn sm" onClick={extract} title="Review facts about you from this chat and choose which to remember"><Brain />Save memories</button>}
          {chatId && <button className="btn ghost sm icon" title="Delete conversation" onClick={deleteChat}><Trash2 /></button>}
        </div>
        <div className="chat-scroll" ref={scroller}>
          <div className="chat-thread">
            {!chatId && !sending && (
              <Empty>
                <div style={{ fontSize: 16, color: "var(--ink)", marginBottom: 6 }}>Ask about anything in your vault</div>
                Answers are grounded in your notes and memories and cite them. The assistant can also create, edit and link notes for you;
                deletions wait for your approval.
                {system.data && !system.data.ai.configured && <div className="small" style={{ marginTop: 10, color: "var(--warn)" }}>AI is not configured — you'll get the most relevant passages instead.</div>}
              </Empty>
            )}
            {messages.data?.map((m) => (
              <div key={m.id} className={`msg ${m.role}`}>
                {m.role === "user" ? <div className="chat-bubble">{m.content}</div> : (
                  <>
                    <div className="chat-bubble prose-lite" onClick={(e) => onBubbleClick(e, m)} dangerouslySetInnerHTML={{ __html: renderChatMarkdown(m.content) }} />
                    {m.meta.actions?.map((a, i) => (
                      <div key={i} className={`action ${a.status === "needs_confirmation" && a.pending_id && openIds.has(a.pending_id) ? "pending" : ""}`}>
                        {a.status === "failed" ? <X size={14} color="var(--bad)" /> : <Check size={14} color={a.status === "needs_confirmation" ? "var(--warn)" : "var(--good)"} />}
                        <span className="grow"><code>{a.tool}</code>{" "}
                          {a.summary ?? (a.path ? <Link to={noteHref(a.path)}>{a.path}</Link> : typeof a.args.query === "string" ? <span className="muted">“{a.args.query}”</span> : null)}
                          {a.error && <span style={{ color: "var(--bad)" }}> — {a.error}</span>}
                        </span>
                        {a.status === "needs_confirmation" && a.pending_id && openIds.has(a.pending_id) && (
                          <><button className="btn sm danger" onClick={() => decide(a, true)}>Approve</button><button className="btn sm" onClick={() => decide(a, false)}>Reject</button></>
                        )}
                      </div>
                    ))}
                    {!!m.meta.sources?.length && (
                      <div className="msg-meta">
                        {m.meta.sources.map((s) => s.path
                          ? <Link key={s.n} className="chip" to={noteHref(s.path)} title={s.heading || s.path}><FileText size={11} />{s.n}. {s.title}</Link>
                          : <span key={s.n} className="chip">{s.n}. {s.label}</span>)}
                      </div>
                    )}
                    <div className="msg-meta">
                      {m.meta.model && <span>{m.meta.model}</span>}
                      {m.meta.usage && m.meta.usage.calls > 0 && <span>· {(m.meta.usage.input_tokens + m.meta.usage.output_tokens).toLocaleString()} tokens</span>}
                      {m.meta.context_tokens ? <span>· context {m.meta.context_tokens.toLocaleString()}</span> : null}
                    </div>
                  </>
                )}
              </div>
            ))}
            {sending && <><div className="msg user"><div className="chat-bubble">{sending}</div></div><div className="msg assistant"><div className="typing"><i /><i /><i /></div></div></>}
          </div>
        </div>
        <div className="composer">
          {!!atts.data?.length && (
            <div className="row wrap" style={{ maxWidth: 760, margin: "0 auto 8px", gap: 6 }}>
              {atts.data.map((a) => (
                <span key={a.id} className="chip" title={`${a.chars ?? 0} characters extracted`}>
                  <Paperclip size={11} />{a.filename} <span className="muted">{bytes(a.size)}</span>
                  {a.note_path ? <Link to={noteHref(a.note_path)}>open</Link> : (
                    <button className="btn ghost sm" style={{ height: 18, padding: "0 4px" }} onClick={async () => {
                      const dest = await prompt("Save to vault", { label: "Note path", value: `Inbox/${a.filename.replace(/\.[^.]+$/, "")}` });
                      if (!dest) return;
                      try { const r = await api.post<{ path: string }>(`/api/attachments/${a.id}/save`, { destination: dest }); refresh(); qc.invalidateQueries({ queryKey: ["chat-attachments", chatId] }); toast("Saved", { action: { label: "Open", run: () => nav(noteHref(r.path)) } }); }
                      catch (e) { toast(errorText(e), { kind: "error" }); }
                    }}>save</button>
                  )}
                </span>
              ))}
            </div>
          )}
          <div className="composer-inner">
            <textarea rows={Math.min(8, Math.max(1, draft.split("\n").length))} placeholder="Ask, or tell it what to do with your notes…" value={draft}
                      onChange={(e) => setDraft(e.target.value)}
                      onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); send(draft); } }} />
            <div className="spread">
              <div className="row" style={{ gap: 2 }}>
                <button className="btn ghost sm icon" title="Attach a file" onClick={() => fileInput.current?.click()}><Paperclip /></button>
                <button className="btn ghost sm icon" title="Import a web page" onClick={addLink}><Link2 /></button>
                <input ref={fileInput} type="file" hidden onChange={(e) => { const f = e.target.files?.[0]; if (f) upload(f); e.target.value = ""; }} />
              </div>
              <button className="btn primary sm" disabled={!draft.trim() || !!sending} onClick={() => send(draft)}><Send />Send</button>
            </div>
          </div>
        </div>
      </section>
      {candidates && (
        <Modal title="Remember these?" onClose={() => setCandidates(null)} wide footer={<><button className="btn" onClick={() => setCandidates(null)}>Cancel</button><button className="btn primary" onClick={saveCandidates}>Save selected</button></>}>
          <p className="muted small" style={{ marginTop: 0 }}>Facts you stated in this chat. Nothing is saved until you choose.</p>
          <div className="stack" style={{ gap: 6 }}>
            {candidates.map((c, i) => (
              <label key={i} className="row card" style={{ padding: "8px 10px", alignItems: "flex-start" }}>
                <input type="checkbox" checked={!!c.keep} onChange={(e) => setCandidates((cs) => cs!.map((x, j) => (j === i ? { ...x, keep: e.target.checked } : x)))} style={{ marginTop: 3 }} />
                <span className="grow">{c.content}<div className="muted small">{c.category}{c.duplicate_of ? " · already saved" : ""}</div></span>
              </label>
            ))}
          </div>
        </Modal>
      )}
    </div>
  );
}
