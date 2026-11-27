import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { motion } from "motion/react";
import { noteHref } from "../lib/api";
import { folderColor } from "../lib/colors";
import type { GraphData } from "../lib/types";

const W = 440, H = 420, MAX_ISLANDS = 8, MAX_DOTS = 36;

interface Dot { id: string; label: string; degree: number; x: number; y: number; island: string }
interface Isle { name: string; color: string; count: number; r: number; x: number; y: number; dots: Dot[] }

/** Folders as islands: notes spiral out from the most-linked one, islands are packed without overlap. */
function layout(data: GraphData): { isles: Isle[]; links: [Dot, Dot][] } {
  const notes = data.nodes.filter((n) => n.type === "note");
  const groups = new Map<string, typeof notes>();
  for (const n of notes) {
    const top = (n.folder || "").split("/")[0] || "Vault";
    if (!groups.has(top)) groups.set(top, []);
    groups.get(top)!.push(n);
  }
  let entries = [...groups.entries()].sort((a, b) => b[1].length - a[1].length);
  if (entries.length > MAX_ISLANDS) {
    const rest = entries.slice(MAX_ISLANDS - 1).flatMap(([, v]) => v);
    entries = [...entries.slice(0, MAX_ISLANDS - 1), ["Other", rest]];
  }
  const isles: Isle[] = entries.map(([name, ns], i) => {
    const sorted = [...ns].sort((a, b) => b.degree - a.degree).slice(0, MAX_DOTS);
    const r = Math.max(34, 15 * Math.sqrt(sorted.length) + 24);
    const a = i * 2.4, d = i === 0 ? 0 : 60 + 26 * Math.sqrt(i);
    return { name, color: folderColor(name === "Other" || name === "Vault" ? "" : name), count: ns.length, r, x: d * Math.cos(a), y: d * Math.sin(a),
      dots: sorted.map((n) => ({ id: n.id, label: n.label, degree: n.degree, x: 0, y: 0, island: name })) };
  });
  // Relax: push overlapping islands apart, pull everything gently to the middle.
  for (let it = 0; it < 500; it++) {
    for (let i = 0; i < isles.length; i++) {
      const a = isles[i];
      a.x *= 0.996; a.y *= 0.996;
      for (let j = i + 1; j < isles.length; j++) {
        const b = isles[j];
        const dx = b.x - a.x, dy = b.y - a.y, dist = Math.hypot(dx, dy) || 0.01, min = a.r + b.r + 30;
        if (dist < min) {
          const push = (min - dist) / 2, ux = dx / dist, uy = dy / dist;
          a.x -= ux * push; a.y -= uy * push; b.x += ux * push; b.y += uy * push;
        }
      }
    }
  }
  // Fit into the view box, leaving room for labels under each island.
  const minX = Math.min(...isles.map((s) => s.x - s.r)), maxX = Math.max(...isles.map((s) => s.x + s.r));
  const minY = Math.min(...isles.map((s) => s.y - s.r)), maxY = Math.max(...isles.map((s) => s.y + s.r + 22));
  const k = Math.min(1.35, (W - 20) / (maxX - minX || 1), (H - 20) / (maxY - minY || 1));
  const ox = W / 2 - ((minX + maxX) / 2) * k, oy = H / 2 - ((minY + maxY) / 2) * k;
  const byId = new Map<string, Dot>();
  for (const s of isles) {
    s.x = s.x * k + ox; s.y = s.y * k + oy; s.r *= k;
    s.dots.forEach((d, i) => {
      const rr = i ? 15 * k * Math.sqrt(i) + 6 * k : 0, ang = i * 2.39996;
      d.x = s.x + rr * Math.cos(ang); d.y = s.y + rr * Math.sin(ang);
      byId.set(d.id, d);
    });
  }
  const links: [Dot, Dot][] = [];
  for (const e of data.edges) {
    const a = byId.get(e.source), b = byId.get(e.target);
    if (a && b && a !== b) links.push([a, b]);
  }
  return { isles, links };
}

export default function Islands({ data }: { data?: GraphData }) {
  const nav = useNavigate();
  const [hover, setHover] = useState<string | null>(null);
  const { isles, links } = useMemo(() => (data ? layout(data) : { isles: [], links: [] }), [data]);
  if (!data) return <div className="map-empty">Mapping your vault…</div>;
  if (isles.length === 0) return <div className="map-empty">Your notes will gather here as islands, one per folder.</div>;

  return (
    <svg className={`islands ${hover ? "hovering" : ""}`} viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Notes grouped by folder">
      {isles.map((s, i) => (
        <motion.g key={`h-${s.name}`} style={{ transformOrigin: `${s.x}px ${s.y}px` }}
          initial={{ scale: 0, opacity: 0 }} animate={{ scale: 1, opacity: 1 }}
          transition={{ delay: 0.12 + i * 0.07, type: "spring", bounce: 0.45, duration: 0.8 }}>
          <circle cx={s.x} cy={s.y} r={s.r} fill={s.color} fillOpacity={0.1} className={`halo ${hover === s.name ? "on" : ""}`}
            onMouseEnter={() => setHover(s.name)} onMouseLeave={() => setHover(null)} style={{ cursor: "pointer" }} />
        </motion.g>
      ))}
      {links.map(([a, b], i) => {
        const hot = hover !== null && (a.island === hover || b.island === hover);
        const mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2 - Math.hypot(b.x - a.x, b.y - a.y) * 0.12;
        return (
          <motion.path key={`${a.id}>${b.id}`} className={`link ${hot ? "hot" : ""}`} d={`M${a.x},${a.y} Q${mx},${my} ${b.x},${b.y}`}
            initial={{ pathLength: 0 }} animate={{ pathLength: 1 }} transition={{ delay: 0.55 + i * 0.02, duration: 0.9, ease: [0.22, 1, 0.36, 1] }} />
        );
      })}
      {isles.map((s, i) => (
        <g key={s.name} className={`island ${hover === s.name ? "on" : ""}`} onMouseEnter={() => setHover(s.name)} onMouseLeave={() => setHover(null)}>
          {s.dots.map((d, j) => (
            <motion.circle key={d.id} className="dot" cx={d.x} cy={d.y} r={3.5 + Math.sqrt(d.degree) * 2.1} fill={s.color}
              style={{ transformOrigin: `${d.x}px ${d.y}px`, cursor: "pointer" }}
              initial={{ scale: 0 }} animate={{ scale: 1 }} whileHover={{ scale: 1.5 }}
              transition={{ type: "spring", stiffness: 420, damping: 14, delay: 0.3 + i * 0.07 + j * 0.025 }}
              onClick={() => nav(noteHref(d.id))}>
              <title>{d.label}</title>
            </motion.circle>
          ))}
          {s.dots[0] && s.dots[0].degree >= 2 && (
            <motion.text className="top" x={s.dots[0].x} y={s.dots[0].y - 11 - Math.sqrt(s.dots[0].degree) * 2} textAnchor="middle"
              initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.7 + i * 0.07 }}>{s.dots[0].label}</motion.text>
          )}
          <motion.text className="lbl" x={s.x} y={s.y + s.r + 17} textAnchor="middle" fill={s.color}
            initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: 0.5 + i * 0.07 }}>
            {s.name} <tspan fillOpacity={0.6}>{s.count}</tspan>
          </motion.text>
        </g>
      ))}
    </svg>
  );
}
