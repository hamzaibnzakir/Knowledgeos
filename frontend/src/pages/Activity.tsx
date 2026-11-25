import { useInfiniteQuery } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import { api, noteHref, qs } from "../lib/api";
import type { AuditEvent } from "../lib/types";
import { Empty } from "../components/ui";

export default function ActivityPage() {
  const [params, setParams] = useSearchParams();
  const actor = params.get("actor") ?? "";
  const q = useInfiniteQuery({
    queryKey: ["audit", actor],
    initialPageParam: 0,
    queryFn: ({ pageParam }) => api.get<AuditEvent[]>(`/api/audit${qs({ limit: 100, actor, before: pageParam || undefined })}`),
    getNextPageParam: (last) => (last.length === 100 ? last[last.length - 1].id : undefined),
  });
  const rows = q.data?.pages.flat() ?? [];

  return (
    <div className="page">
      <div className="page-inner">
        <div className="spread">
          <div><h1 className="title">Activity</h1><div className="muted">Every change to your vault and memory, and who made it.</div></div>
          {actor && <button className="btn sm" onClick={() => setParams({})}>Show everyone</button>}
        </div>
        {actor && <p className="small">Filtered to <code>{actor}</code></p>}
        <div className="card scroll-x" style={{ marginTop: 14 }}>
          {q.isLoading ? <Empty>Loading…</Empty> : rows.length === 0 ? <Empty>No activity recorded.</Empty> : (
            <table className="tbl">
              <thead><tr><th>When</th><th>Action</th><th>Who</th><th>Details</th></tr></thead>
              <tbody>
                {rows.map((e) => {
                  const path = typeof e.details.path === "string" ? e.details.path : typeof e.details.to === "string" ? e.details.to : null;
                  return (
                    <tr key={e.id}>
                      <td className="small muted" style={{ whiteSpace: "nowrap" }}>{new Date(e.created_at).toLocaleString()}</td>
                      <td><code>{e.action}</code>{e.status !== "completed" && <span className={`chip ${e.status === "failed" || e.status === "blocked" ? "bad" : "warn"}`} style={{ marginLeft: 6 }}>{e.status}</span>}</td>
                      <td className="small"><button className="btn ghost sm" style={{ padding: 0, height: "auto" }} onClick={() => setParams({ actor: e.actor })}>{e.actor}</button></td>
                      <td className="small">{path && !String(e.action).endsWith("delete") ? <Link to={noteHref(path)}>{path}</Link> : path}
                        {!path && Object.keys(e.details).length > 0 && <span className="muted">{JSON.stringify(e.details).slice(0, 120)}</span>}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>
        {q.hasNextPage && <div style={{ textAlign: "center", marginTop: 12 }}><button className="btn" onClick={() => q.fetchNextPage()} disabled={q.isFetchingNextPage}>Load more</button></div>}
      </div>
    </div>
  );
}
