import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import cytoscape, { type Core } from "cytoscape";
import { Maximize, X } from "lucide-react";
import { api, noteHref, qs } from "../lib/api";
import type { GraphData } from "../lib/types";
import { folderColor } from "../lib/colors";
import { useTheme } from "../components/ui";

export default function GraphPage() {
  const [params, setParams] = useSearchParams();
  const focus = params.get("focus") ?? "";
  const [depth, setDepth] = useState(2);
  const [tags, setTags] = useState(false);
  const [unresolved, setUnresolved] = useState(false);
  const [filter, setFilter] = useState("");
  const nav = useNavigate();
  const [theme] = useTheme();
  const box = useRef<HTMLDivElement>(null);
  const cy = useRef<Core | null>(null);

  const data = useQuery({
    queryKey: ["graph", focus, depth, tags, unresolved],
    queryFn: () => focus
      ? api.get<GraphData>(`/api/graph/local${qs({ path: focus, depth, tags })}`)
      : api.get<GraphData>(`/api/graph${qs({ tags, unresolved })}`),
  });

  const folders = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const n of data.data?.nodes ?? []) if (n.type === "note") { const f = (n.folder ?? "").split("/")[0] || "(root)"; counts[f] = (counts[f] ?? 0) + 1; }
    return Object.entries(counts).sort((a, b) => b[1] - a[1]).map(([f]) => f);
  }, [data.data]);

  useEffect(() => {
    if (!box.current || !data.data) return;
    const css = getComputedStyle(document.documentElement);
    const ink = css.getPropertyValue("--ink").trim();
    const muted = css.getPropertyValue("--muted").trim();
    const line = css.getPropertyValue("--line-2").trim();
    const accent = css.getPropertyValue("--accent").trim();
    const maxDeg = Math.max(1, ...data.data.nodes.map((n) => n.degree));
    const elements = [
      ...data.data.nodes.map((n) => ({
        data: {
          id: n.id, label: n.label, type: n.type,
          color: n.type === "tag" ? muted : n.type === "missing" ? line : folderColor(n.folder || ""),
          size: 10 + 22 * Math.sqrt(n.degree / maxDeg),
        },
      })),
      ...data.data.edges.map((e, i) => ({ data: { id: `e${i}`, source: e.source, target: e.target, type: e.type } })),
    ];
    cy.current?.destroy();
    const inst = cytoscape({
      container: box.current,
      elements,
      minZoom: 0.15,
      maxZoom: 3,
      wheelSensitivity: 0.25,
      style: [
        { selector: "node", style: { "background-color": "data(color)", width: "data(size)", height: "data(size)", label: "data(label)", "font-size": 10, color: ink, "text-valign": "bottom", "text-margin-y": 4, "text-max-width": "120px", "text-wrap": "ellipsis", "min-zoomed-font-size": 7, "border-width": 0 } },
        { selector: "node[type='tag']", style: { shape: "round-rectangle", "font-size": 9, color: muted } },
        { selector: "node[type='missing']", style: { "border-width": 1, "border-style": "dashed", "border-color": muted, "background-opacity": 0.2, color: muted } },
        { selector: "edge", style: { width: 1, "line-color": line, "curve-style": "haystack", opacity: 0.8 } },
        { selector: "edge[type='tag']", style: { "line-style": "dotted" } },
        { selector: "edge[type='unresolved']", style: { "line-style": "dashed" } },
        { selector: ".faded", style: { opacity: 0.12 } },
        { selector: ".hl", style: { "border-width": 3, "border-color": accent } },
        { selector: `node[id = ${JSON.stringify(focus)}]`, style: { "border-width": 3, "border-color": accent } },
      ],
      layout: { name: "cose", animate: false, nodeRepulsion: () => 9000, idealEdgeLength: () => 70, gravity: 0.25, numIter: 1200, padding: 40 } as cytoscape.LayoutOptions,
    });
    inst.one("layoutstop", () => {
      if (inst.zoom() > 1.1) { inst.zoom(1.1); inst.center(); }
    });
    if (inst.zoom() > 1.1) { inst.zoom(1.1); inst.center(); }
    inst.on("tap", "node", (evt) => {
      const n = evt.target;
      const type = n.data("type");
      if (type === "note") nav(noteHref(n.id()));
      else if (type === "tag") nav(`/search${qs({ tag: String(n.data("label")).replace(/^#/, "") })}`);
    });
    inst.on("mouseover", "node", (evt) => {
      const hood = evt.target.closedNeighborhood();
      inst.elements().not(hood).addClass("faded");
    });
    inst.on("mouseout", "node", () => inst.elements().removeClass("faded"));
    cy.current = inst;
    return () => { inst.destroy(); if (cy.current === inst) cy.current = null; };
  }, [data.data, folders, nav, theme, focus]);

  useEffect(() => {
    const inst = cy.current;
    if (!inst) return;
    inst.nodes().removeClass("hl faded");
    const f = filter.trim().toLowerCase();
    if (!f) return;
    const hits = inst.nodes().filter((n) => String(n.data("label")).toLowerCase().includes(f));
    inst.nodes().not(hits).addClass("faded");
    hits.addClass("hl");
    if (hits.length) inst.animate({ fit: { eles: hits, padding: 80 } }, { duration: 300 });
  }, [filter, data.data]);

  const notes = data.data?.nodes.filter((n) => n.type === "note").length ?? 0;
  return (
    <div className="page" style={{ overflow: "hidden" }}>
      <div className="graph-wrap">
        <div className="graph-controls">
          <input className="input" style={{ width: 200, height: 32 }} placeholder="Highlight…" value={filter} onChange={(e) => setFilter(e.target.value)} />
          {focus ? (
            <>
              <span className="chip accent" style={{ height: 32 }}>Around {focus.replace(/\.md$/, "").split("/").pop()}
                <button className="btn ghost sm icon" style={{ height: 18, width: 18 }} onClick={() => setParams({})}><X size={12} /></button></span>
              <div className="seg">{[1, 2, 3].map((d) => <button key={d} className={depth === d ? "on" : ""} onClick={() => setDepth(d)}>{d} hop{d > 1 ? "s" : ""}</button>)}</div>
            </>
          ) : (
            <button className={`btn sm ${unresolved ? "primary" : ""}`} onClick={() => setUnresolved((v) => !v)}>Missing notes</button>
          )}
          <button className={`btn sm ${tags ? "primary" : ""}`} onClick={() => setTags((v) => !v)}>Tags</button>
          <button className="btn sm icon" title="Fit" onClick={() => cy.current?.fit(undefined, 40)}><Maximize /></button>
        </div>
        <div ref={box} className="graph-canvas" />
        <div className="graph-legend card card-pad" style={{ padding: "8px 10px" }}>
          <div className="small muted" style={{ marginBottom: 4 }}>{notes} notes · {data.data?.edges.length ?? 0} links</div>
          <div className="row wrap" style={{ gap: 8, maxWidth: 320 }}>
            {folders.slice(0, 8).map((f) => <span key={f} className="row small" style={{ gap: 4 }}><i style={{ width: 8, height: 8, borderRadius: 4, background: folderColor(f === "(root)" ? "" : f), display: "inline-block" }} />{f}</span>)}
          </div>
        </div>
        {data.data && notes === 0 && <div className="empty" style={{ position: "absolute", inset: 0, display: "grid", placeItems: "center" }}>No notes to show yet. Links between notes ([[Title]]) appear here.</div>}
      </div>
    </div>
  );
}
