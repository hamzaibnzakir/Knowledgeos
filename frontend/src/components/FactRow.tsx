import { useRef, useState } from "react";
import { Check, Pencil, Undo2, X } from "lucide-react";
import { animate, motion, useMotionValue, useTransform } from "motion/react";
import { api } from "../lib/api";
import { useCoarsePointer, useMemoryRefresh } from "../lib/hooks";
import type { Fact } from "../lib/types";
import { dayLabel, errorText, useToast } from "./ui";

const SWIPE = 96;

/** One memory fact: the statement first, provenance underneath, actions on the right.
 *  On touch screens a fact waiting for review can be swiped: right to keep, left to reject. */
export default function FactRow({ fact, showSubject = false }: { fact: Fact; showSubject?: boolean }) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(fact.text);
  const [busy, setBusy] = useState(false);
  const [kept, setKept] = useState(false);
  const refresh = useMemoryRefresh();
  const toast = useToast();
  const touch = useCoarsePointer();
  const old = fact.status !== "current";
  const pending = fact.review === "new" && !old;
  const swipeable = touch && pending && !editing;
  const x = useMotionValue(0);
  const keepOpacity = useTransform(x, [12, SWIPE], [0, 1]);
  const rejectOpacity = useTransform(x, [-SWIPE, -12], [1, 0]);
  const keepScale = useTransform(x, [12, SWIPE], [0.6, 1]);
  const rejectScale = useTransform(x, [-SWIPE, -12], [1, 0.6]);
  const armed = useRef(0);

  const run = async (fn: () => Promise<unknown>, done: string) => {
    setBusy(true);
    try { await fn(); toast(done); refresh(); } catch (e) { toast(errorText(e), { kind: "error" }); animate(x, 0); } finally { setBusy(false); }
  };
  const keep = () => { setKept(true); run(() => api.post(`/api/memory/facts/${fact.id}/approve`), "Kept"); };
  const reject = () => run(() => api.post(`/api/memory/facts/${fact.id}/reject`), "Rejected");
  const save = () => run(async () => {
    await api.patch(`/api/memory/facts/${fact.id}`, { fact: text, in_place: fact.review === "new" });
    if (fact.review === "new") await api.post(`/api/memory/facts/${fact.id}/approve`);
    setEditing(false);
  }, "Fact updated");

  const by = !fact.by_agent || fact.by_agent === "you" ? "You" : fact.by_agent;
  const body = (
    <>
      <div className="fact-main">
        {editing ? (
          <form onSubmit={(e) => { e.preventDefault(); save(); }} className="row">
            <input className="input" autoFocus value={text} onChange={(e) => setText(e.target.value)} maxLength={600} />
            <button className="btn sm primary" disabled={busy || !text.trim()}>Save</button>
            <button type="button" className="btn sm ghost" onClick={() => { setEditing(false); setText(fact.text); }}>Cancel</button>
          </form>
        ) : <div className="fact-text">{fact.text}</div>}
        <div className="fact-meta">
          {showSubject && <span className="fact-subject">{fact.subject}</span>}
          <span>{by}{fact.on_date ? `, ${dayLabel(fact.on_date)}` : ""}</span>
          {pending && <span className="tag-new">{swipeable ? "Swipe to decide" : "Needs review"}</span>}
          {fact.flag && <span className="tag-flag">Looks like an instruction</span>}
          {fact.external && <span className="tag-ext">From a web page or document</span>}
          {fact.src && <span className="fact-src" title={fact.src}>Source: {fact.src}</span>}
          {old && <span>{fact.replaced_by ? "Replaced" : "Retired"} {fact.until && dayLabel(fact.until)}{fact.reason && fact.reason !== "replaced" ? `: ${fact.reason}` : ""}</span>}
        </div>
      </div>
      {!editing && !old && (
        <div className="fact-actions">
          {pending && !swipeable && (
            <button className="btn sm" disabled={busy} title={fact.flag ? "Keep it even though it looks like an instruction" : "Keep this fact"} onClick={keep}>
              <Check />{fact.flag ? "Keep anyway" : "Keep"}</button>
          )}
          <button className="btn sm ghost icon" title="Edit" onClick={() => setEditing(true)}><Pencil /></button>
          {!swipeable && (
            <button className="btn sm ghost icon" title={pending ? "Reject" : "Retire (moves to History)"} disabled={busy}
                    onClick={() => pending ? reject() : run(() => api.post(`/api/memory/facts/${fact.id}/forget`, { reason: "retired by owner" }), "Moved to History")}>
              {pending ? <X /> : <Undo2 />}
            </button>
          )}
        </div>
      )}
    </>
  );

  return (
    <motion.div layout="position" className={`fact ${old ? "old" : ""} ${pending ? "unreviewed" : ""} ${fact.flag ? "flagged" : ""} ${kept ? "kept" : ""} ${swipeable ? "swipe" : ""}`}
      initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, height: 0, paddingTop: 0, paddingBottom: 0, borderBottomWidth: 0, transition: { duration: 0.3, ease: [0.4, 0, 0.2, 1] } }}
      transition={{ type: "spring", stiffness: 340, damping: 30 }}>
      {swipeable ? (
        <>
          <motion.div className="swipe-under keep" style={{ opacity: keepOpacity }}><motion.span style={{ scale: keepScale }}><Check />Keep</motion.span></motion.div>
          <motion.div className="swipe-under reject" style={{ opacity: rejectOpacity }}><motion.span style={{ scale: rejectScale }}>Reject<X /></motion.span></motion.div>
          <motion.div className="swipe-card" style={{ x }} drag="x" dragDirectionLock dragMomentum={false}
            onDrag={(_, i) => {
              const side = i.offset.x > SWIPE ? 1 : i.offset.x < -SWIPE ? -1 : 0;
              if (side !== armed.current) { armed.current = side; if (side) navigator.vibrate?.(8); }
            }}
            onDragEnd={(_, i) => {
              armed.current = 0;
              const dir = i.offset.x > SWIPE || i.velocity.x > 700 ? 1 : i.offset.x < -SWIPE || i.velocity.x < -700 ? -1 : 0;
              if (!dir || busy) { animate(x, 0, { type: "spring", stiffness: 500, damping: 32 }); return; }
              animate(x, dir * window.innerWidth, { type: "spring", stiffness: 260, damping: 30 });
              if (dir > 0) keep(); else reject();
            }}>
            {body}
          </motion.div>
        </>
      ) : body}
    </motion.div>
  );
}
