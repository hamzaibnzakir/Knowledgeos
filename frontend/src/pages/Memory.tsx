import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Archive, Pencil, Pin, RotateCcw, Trash2 } from "lucide-react";
import { api, qs } from "../lib/api";
import type { Memory } from "../lib/types";
import { Empty, errorText, timeAgo, useDebounced, useDialog, useToast } from "../components/ui";

const CATEGORIES = ["profile", "preference", "project", "business", "technical", "goal", "communication", "general"];

export default function MemoryPage() {
  const [status, setStatus] = useState<"active" | "archived">("active");
  const [category, setCategory] = useState("");
  const [q, setQ] = useState("");
  const dq = useDebounced(q, 250);
  const [content, setContent] = useState("");
  const [newCat, setNewCat] = useState("general");
  const qc = useQueryClient();
  const toast = useToast();
  const { prompt, confirm } = useDialog();
  const list = useQuery({ queryKey: ["memories", status, category, dq], queryFn: () => api.get<Memory[]>(`/api/memories${qs({ status, category, q: dq })}`) });
  const reload = () => qc.invalidateQueries({ queryKey: ["memories"] });

  const add = async () => {
    try {
      const r = await api.post<{ created: boolean }>("/api/memories", { content, category: newCat });
      toast(r.created ? "Remembered" : "Already remembered — nothing duplicated");
      setContent("");
      reload();
    } catch (e) { toast(errorText(e), { kind: "error" }); }
  };
  const edit = async (m: Memory) => {
    const v = await prompt("Edit memory", { value: m.content });
    if (v && v !== m.content) { await api.patch(`/api/memories/${m.id}`, { content: v }); reload(); }
  };
  const act = async (url: string, msg: string) => { await api.post(url); toast(msg); reload(); };
  const purge = async (m: Memory) => {
    if (await confirm("Delete permanently?", { body: m.content, danger: true, confirm: "Delete" })) { await api.del(`/api/memories/${m.id}`); reload(); }
  };

  return (
    <div className="page">
      <div className="page-inner narrow">
        <h1 className="title">Memory</h1>
        <p className="muted" style={{ marginTop: 0 }}>Short, durable facts the assistant and your agents use. Nothing is saved automatically — only what you add, ask an agent to remember, or approve from a chat.</p>

        <div className="card card-pad stack" style={{ marginBottom: 18 }}>
          <textarea className="textarea" rows={2} placeholder="e.g. Prefers OpenAI for the main agent" value={content} onChange={(e) => setContent(e.target.value)} />
          <div className="spread">
            <select className="select" style={{ width: "auto" }} value={newCat} onChange={(e) => setNewCat(e.target.value)}>
              {CATEGORIES.map((c) => <option key={c}>{c}</option>)}
            </select>
            <button className="btn primary" disabled={!content.trim()} onClick={add}>Remember</button>
          </div>
        </div>

        <div className="row wrap" style={{ marginBottom: 10 }}>
          <div className="seg">
            <button className={status === "active" ? "on" : ""} onClick={() => setStatus("active")}>Active</button>
            <button className={status === "archived" ? "on" : ""} onClick={() => setStatus("archived")}>Forgotten</button>
          </div>
          <select className="select" style={{ width: "auto", height: 30, padding: "2px 8px" }} value={category} onChange={(e) => setCategory(e.target.value)}>
            <option value="">All categories</option>
            {CATEGORIES.map((c) => <option key={c}>{c}</option>)}
          </select>
          <input className="input grow" style={{ height: 30 }} placeholder="Filter…" value={q} onChange={(e) => setQ(e.target.value)} />
        </div>

        <div className="card list">
          {list.isLoading ? <Empty>Loading…</Empty> : list.data?.length === 0 ? <Empty>{status === "active" ? "No memories yet." : "Nothing forgotten."}</Empty> :
            list.data?.map((m) => (
              <div key={m.id} className="list-item">
                <div className="spread" style={{ alignItems: "flex-start" }}>
                  <div className="grow">
                    <div>{m.pinned ? <Pin size={12} style={{ marginRight: 4 }} /> : null}{m.content}</div>
                    <div className="row small muted" style={{ marginTop: 4 }}>
                      <span className="chip">{m.category}</span><span>{m.source}</span><span>· {timeAgo(m.updated_at)}</span>
                      {m.confidence < 1 && <span>· {Math.round(m.confidence * 100)}% confident</span>}
                    </div>
                  </div>
                  <div className="row" style={{ gap: 2 }}>
                    {status === "active" ? (
                      <>
                        <button className="btn ghost sm icon" title={m.pinned ? "Unpin" : "Pin (always included)"} onClick={async () => { await api.patch(`/api/memories/${m.id}`, { pinned: !m.pinned }); reload(); }}><Pin /></button>
                        <button className="btn ghost sm icon" title="Edit" onClick={() => edit(m)}><Pencil /></button>
                        <button className="btn ghost sm icon" title="Forget" onClick={() => act(`/api/memories/${m.id}/forget`, "Forgotten (restorable)")}><Archive /></button>
                      </>
                    ) : (
                      <>
                        <button className="btn ghost sm icon" title="Restore" onClick={() => act(`/api/memories/${m.id}/restore`, "Restored")}><RotateCcw /></button>
                        <button className="btn ghost sm icon" title="Delete permanently" onClick={() => purge(m)}><Trash2 /></button>
                      </>
                    )}
                  </div>
                </div>
              </div>
            ))}
        </div>
      </div>
    </div>
  );
}
