import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Search as SearchIcon, X } from "lucide-react";
import { api, noteHref, qs } from "../lib/api";
import { highlightSnippet } from "../lib/markdown";
import type { NoteSummary, SearchHit } from "../lib/types";
import { Empty, timeAgo, useDebounced } from "../components/ui";

export default function SearchPage() {
  const [params, setParams] = useSearchParams();
  const [q, setQ] = useState(params.get("q") ?? "");
  const tag = params.get("tag") ?? "";
  const mode = params.get("mode") ?? "hybrid";
  const dq = useDebounced(q, 250);
  useEffect(() => {
    const next: Record<string, string> = {};
    if (dq) next.q = dq;
    if (tag) next.tag = tag;
    if (mode !== "hybrid") next.mode = mode;
    setParams(next, { replace: true });
  }, [dq, tag, mode, setParams]);

  const results = useQuery({
    queryKey: ["search", dq, tag, mode],
    queryFn: () => api.get<SearchHit[]>(`/api/search${qs({ q: dq, tag, mode, limit: 50 })}`),
    enabled: dq.trim().length > 0,
  });
  const tagged = useQuery({
    queryKey: ["notes", "tag", tag],
    queryFn: () => api.get<NoteSummary[]>(`/api/notes${qs({ tag, limit: 500 })}`),
    enabled: !!tag && !dq.trim(),
  });
  const tags = useQuery({ queryKey: ["tags"], queryFn: () => api.get<{ tag: string; count: number }[]>("/api/tags") });

  const setMode = (m: string) => setParams({ ...(dq ? { q: dq } : {}), ...(tag ? { tag } : {}), ...(m !== "hybrid" ? { mode: m } : {}) }, { replace: true });
  const setTag = (t: string) => setParams({ ...(dq ? { q: dq } : {}), ...(t ? { tag: t } : {}), ...(mode !== "hybrid" ? { mode } : {}) }, { replace: true });

  return (
    <div className="page">
      <div className="page-inner narrow">
        <div className="searchbox">
          <SearchIcon size={18} className="muted" />
          <input autoFocus placeholder="Search by words or meaning…" value={q} onChange={(e) => setQ(e.target.value)} />
          {q && <button className="btn ghost sm icon" onClick={() => setQ("")} aria-label="Clear"><X /></button>}
        </div>
        <div className="spread" style={{ margin: "10px 0 14px" }}>
          <div className="seg">
            {["hybrid", "keyword", "semantic"].map((m) => <button key={m} className={mode === m ? "on" : ""} onClick={() => setMode(m)}>{m}</button>)}
          </div>
          {tag && <span className="chip accent">#{tag} <button className="btn ghost sm icon" style={{ height: 18, width: 18 }} onClick={() => setTag("")}><X size={12} /></button></span>}
        </div>

        {dq.trim() ? (
          <div className="card">
            {results.isLoading ? <Empty>Searching…</Empty> : results.data?.length === 0 ? <Empty>No matches. Try different words, or switch to semantic.</Empty> :
              results.data?.map((h) => (
                <Link key={h.path} to={noteHref(h.path)} className="hit">
                  <div className="spread">
                    <b>{h.title}</b>
                    <span className="row small muted">{h.match !== "keyword" && <span className="chip">{h.match === "semantic" ? "by meaning" : "words + meaning"}</span>}{timeAgo(h.updated_at)}</span>
                  </div>
                  <div className="small muted">{h.path}</div>
                  <div className="s" dangerouslySetInnerHTML={{ __html: highlightSnippet(h.snippet) }} />
                </Link>
              ))}
          </div>
        ) : tag ? (
          <div className="card list">
            {tagged.data?.map((n) => <Link key={n.path} to={noteHref(n.path)} className="list-item"><div className="t">{n.title}</div><div className="s">{n.excerpt}</div></Link>)}
          </div>
        ) : (
          <>
            <h2 className="section">Tags</h2>
            <div className="row wrap" style={{ gap: 6 }}>
              {tags.data?.length ? tags.data.map((t) => <button key={t.tag} className="chip" onClick={() => setTag(t.tag)}>#{t.tag} <span className="muted">{t.count}</span></button>)
                : <span className="muted">No tags yet. Add #tags in notes or `tags:` in front matter.</span>}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
