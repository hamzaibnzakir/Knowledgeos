import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { noteHref } from "../lib/api";
import { folderColor } from "../lib/colors";
import type { GraphData } from "../lib/types";

interface P { id: string; label: string; folder: string; degree: number; x: number; y: number; vx: number; vy: number; recent: boolean }

/** A small, self-contained force layout: the vault drawn as a night sky. */
function layout(data: GraphData, recent: Set<string>, w: number, h: number): P[] {
  const notes = data.nodes.filter((n) => n.type === "note").slice(0, 160);
  const idx = new Map<string, number>();
  let seed = 7;
  const rand = () => ((seed = (seed * 16807) % 2147483647) / 2147483647);
  const pts: P[] = notes.map((n, i) => {
    idx.set(n.id, i);
    return { id: n.id, label: n.label, folder: n.folder ?? "", degree: n.degree, x: w * (0.15 + 0.7 * rand()), y: h * (0.15 + 0.7 * rand()), vx: 0, vy: 0, recent: recent.has(n.id) };
  });
  const edges = data.edges.filter((e) => idx.has(e.source) && idx.has(e.target)).map((e) => [idx.get(e.source)!, idx.get(e.target)!] as const);
  const cx = w / 2, cy = (h - 50) / 2;
  for (let it = 0; it < 260; it++) {
    const alpha = 1 - it / 260;
    for (let i = 0; i < pts.length; i++) {
      for (let j = i + 1; j < pts.length; j++) {
        const a = pts[i], b = pts[j];
        let dx = a.x - b.x, dy = a.y - b.y;
        const d2 = Math.max(dx * dx + dy * dy, 25);
        const f = ((w * h) / 90 / d2) * alpha;
        const d = Math.sqrt(d2);
        dx /= d; dy /= d;
        a.vx += dx * f; a.vy += dy * f; b.vx -= dx * f; b.vy -= dy * f;
      }
    }
    for (const [s, t] of edges) {
      const a = pts[s], b = pts[t];
      const dx = b.x - a.x, dy = b.y - a.y;
      const d = Math.sqrt(dx * dx + dy * dy) || 1;
      const f = ((d - 70) / d) * 0.06 * alpha;
      a.vx += dx * f; a.vy += dy * f; b.vx -= dx * f; b.vy -= dy * f;
    }
    for (const p of pts) {
      p.vx += (cx - p.x) * 0.002 * alpha; p.vy += (cy - p.y) * 0.01 * alpha;
      p.x += p.vx; p.y += p.vy; p.vx *= 0.6; p.vy *= 0.6;
      p.x = Math.min(w - 28, Math.max(28, p.x)); p.y = Math.min(h - 76, Math.max(26, p.y)); // bottom band sits under the composer
    }
  }
  return pts;
}

export default function Constellation({ data, recent }: { data?: GraphData; recent: string[] }) {
  const box = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ w: 900, h: 300 });
  const nav = useNavigate();

  useEffect(() => {
    if (!box.current) return;
    const ro = new ResizeObserver(([e]) => setSize({ w: Math.round(e.contentRect.width), h: Math.round(e.contentRect.height) }));
    ro.observe(box.current);
    return () => ro.disconnect();
  }, []);

  const noRecent = useMemo(() => new Set<string>(), []);
  const pts = useMemo(() => (data ? layout(data, noRecent, size.w, size.h) : []), [data, noRecent, size.w, size.h]);
  const byId = useMemo(() => new Map(pts.map((p) => [p.id, p])), [pts]);
  const maxDeg = Math.max(1, ...pts.map((p) => p.degree));
  // Place labels for recent notes only where they don't collide with one already placed.
  const shown = useMemo(() => {
    const boxes: { x1: number; x2: number; y1: number; y2: number }[] = [];
    const out = new Set<string>();
    const order = recent.map((id) => pts.find((p) => p.id === id)).filter(Boolean) as P[];
    for (const p of order) {
      const label = p.label.length > 26 ? 26 : p.label.length;
      const wpx = label * 7.2 + 16;
      const left = p.x > size.w * 0.62;
      const b = { x1: left ? p.x - wpx : p.x, x2: left ? p.x : p.x + wpx, y1: p.y - 10, y2: p.y + 10 };
      if (boxes.some((o) => b.x1 < o.x2 && b.x2 > o.x1 && b.y1 < o.y2 && b.y2 > o.y1)) continue;
      boxes.push(b);
      out.add(p.id);
      if (out.size >= (size.w < 520 ? 3 : 6)) break;
    }
    return out;
  }, [pts, recent, size.w]);
  const links = data?.edges.filter((e) => e.type === "link" && byId.has(e.source) && byId.has(e.target)) ?? [];

  return (
    <div className="sky" ref={box} aria-label="Your notes and the links between them">
      <svg viewBox={`0 0 ${size.w} ${size.h}`} role="img">
        {links.map((e, i) => {
          const a = byId.get(e.source)!, b = byId.get(e.target)!;
          return <line key={i} className="edge" x1={a.x} y1={a.y} x2={b.x} y2={b.y} style={{ animationDelay: `${120 + i * 25}ms` }} />;
        })}
        {pts.map((p, i) => {
          const r = 3.5 + 4 * Math.sqrt(p.degree / maxDeg);
          return (
            <g key={p.id} className={`node ${shown.has(p.id) ? "recent" : ""}`} tabIndex={0} role="link" aria-label={p.label}
               style={{ animationDelay: `${i * 18}ms` }}
               onClick={() => nav(noteHref(p.id))} onKeyDown={(e) => e.key === "Enter" && nav(noteHref(p.id))}>
              <circle cx={p.x} cy={p.y} r={r + 6} fill={folderColor(p.folder)} opacity={0.18} />
              <circle cx={p.x} cy={p.y} r={r} fill={folderColor(p.folder)} />
              {(() => {
                const left = p.x > size.w * 0.62;
                const label = p.label.length > 26 ? p.label.slice(0, 25) + "…" : p.label;
                return <text x={left ? p.x - r - 6 : p.x + r + 6} y={p.y + 4} textAnchor={left ? "end" : "start"}>{label}</text>;
              })()}
            </g>
          );
        })}
      </svg>
      {data && pts.length === 0 && <div className="sky-empty">Your notes will appear here as they link to each other.</div>}
    </div>
  );
}
