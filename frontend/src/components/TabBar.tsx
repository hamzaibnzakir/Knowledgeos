import { useEffect, useRef, useState } from "react";
import { NavLink, useLocation } from "react-router-dom";
import { AnimatePresence, motion, useDragControls } from "motion/react";
import { Home, Inbox, LayoutGrid, Plus, Search } from "lucide-react";
import { api } from "../lib/api";
import { useSystem, useVaultRefresh } from "../lib/hooks";
import { errorText, useToast } from "./ui";

/** Phone navigation: everything you do daily sits under your thumb. */
export default function TabBar({ onMore }: { onMore: () => void }) {
  const system = useSystem();
  const unreviewed = system.data?.stats.unreviewed ?? 0;
  const [capture, setCapture] = useState(false);
  const loc = useLocation();
  useEffect(() => setCapture(false), [loc.pathname]);

  const Tab = ({ to, label, icon, end, badge }: { to: string; label: string; icon: React.ReactNode; end?: boolean; badge?: number }) => (
    <NavLink to={to} end={end} className="tab">
      {({ isActive }) => (
        <>
          {isActive && <motion.span layoutId="tab-pill" className="tab-pill" transition={{ type: "spring", stiffness: 500, damping: 36 }} />}
          <span className="tab-icon">{icon}
            <AnimatePresence>{!!badge && (
              <motion.span key={badge} className="tab-badge" initial={{ scale: 0 }} animate={{ scale: 1 }} exit={{ scale: 0 }}
                transition={{ type: "spring", stiffness: 600, damping: 15 }}>{badge}</motion.span>)}
            </AnimatePresence>
          </span>
          <span className="tab-label">{label}</span>
        </>
      )}
    </NavLink>
  );

  return (
    <>
      <nav className="tabbar" aria-label="Main">
        <Tab to="/" end label="Home" icon={<Home />} />
        <Tab to="/search" label="Search" icon={<Search />} />
        <motion.button className="tab-add" aria-label="Capture a note" onClick={() => setCapture(true)}
          whileTap={{ scale: 0.86, rotate: 90 }} transition={{ type: "spring", stiffness: 500, damping: 18 }}><Plus /></motion.button>
        <Tab to="/review" label="Review" icon={<Inbox />} badge={unreviewed} />
        <button className="tab" onClick={onMore}><span className="tab-icon"><LayoutGrid /></span><span className="tab-label">More</span></button>
      </nav>
      <AnimatePresence>{capture && <CaptureSheet onClose={() => setCapture(false)} />}</AnimatePresence>
    </>
  );
}

function CaptureSheet({ onClose }: { onClose: () => void }) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const ref = useRef<HTMLTextAreaElement>(null);
  const drag = useDragControls();
  const refresh = useVaultRefresh();
  const toast = useToast();
  useEffect(() => { const t = setTimeout(() => ref.current?.focus(), 250); return () => clearTimeout(t); }, []);
  const save = async () => {
    if (!text.trim()) return;
    setBusy(true);
    try {
      await api.post("/api/capture", { text });
      refresh();
      toast("Saved to Inbox");
      onClose();
    } catch (e) { toast(errorText(e), { kind: "error" }); setBusy(false); }
  };
  return (
    <>
      <motion.div className="sheet-backdrop" onClick={onClose} initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} />
      <motion.div className="sheet" role="dialog" aria-modal="true" aria-label="Capture"
        initial={{ y: "100%" }} animate={{ y: 0 }} exit={{ y: "100%" }} transition={{ type: "spring", stiffness: 380, damping: 36 }}
        drag="y" dragControls={drag} dragListener={false} dragConstraints={{ top: 0, bottom: 0 }} dragElastic={{ top: 0.05, bottom: 0.8 }}
        onDragEnd={(_, i) => { if (i.offset.y > 110 || i.velocity.y > 600) onClose(); }}>
        <div className="sheet-grip" onPointerDown={(e) => drag.start(e)}><span /></div>
        <h2 className="sheet-title">Jot it down</h2>
        <textarea ref={ref} className="sheet-text" placeholder="An idea, a link, a thing to remember…" value={text} onChange={(e) => setText(e.target.value)} />
        <div className="spread">
          <span className="small muted">Lands in Inbox. Sort it later.</span>
          <button className="btn primary" disabled={!text.trim() || busy} onClick={save}><Inbox />Save</button>
        </div>
      </motion.div>
    </>
  );
}
