import { useEffect, useRef, type ReactNode } from "react";
import { animate, motion, useInView, useMotionValue, useTransform, type Variants } from "motion/react";

/** Shared motion vocabulary: things rise into place on a spring, in a quick cascade. */
export const spring = { type: "spring", stiffness: 260, damping: 24, mass: 0.9 } as const;

export const cascade: Variants = {
  hidden: {},
  show: { transition: { staggerChildren: 0.06, delayChildren: 0.04 } },
};
export const rise: Variants = {
  hidden: { opacity: 0, y: 22, scale: 0.97 },
  show: { opacity: 1, y: 0, scale: 1, transition: spring },
};

export function Cascade({ children, className, as = "div" }: { children: ReactNode; className?: string; as?: "div" | "ul" | "section" }) {
  const C = motion[as];
  return <C className={className} variants={cascade} initial="hidden" animate="show">{children}</C>;
}

/** Counts up to `value` the first time it is seen, and eases to new values after that. */
export function CountUp({ value, className }: { value: number; className?: string }) {
  const ref = useRef<HTMLSpanElement>(null);
  const inView = useInView(ref, { once: true });
  const mv = useMotionValue(0);
  const text = useTransform(mv, (v) => Math.round(v).toLocaleString());
  useEffect(() => {
    if (!inView) return;
    const c = animate(mv, value, { duration: 1.1, ease: [0.22, 1, 0.36, 1] });
    return () => c.stop();
  }, [value, inView, mv]);
  return <motion.span ref={ref} className={className}>{text}</motion.span>;
}

/** Words of a headline rise one after another from behind a mask. */
export function Headline({ lines, className }: { lines: ReactNode[]; className?: string }) {
  let n = 0;
  return (
    <h1 className={className} aria-label={lines.filter((l) => typeof l === "string").join(" ")}>
      {lines.map((line, li) => {
        const words = typeof line === "string" ? line.split(" ") : [line];
        const inner = words.map((w, wi) => (
          <span key={wi} style={{ display: "inline-block", overflow: "hidden", verticalAlign: "top", paddingBottom: "0.08em", marginBottom: "-0.08em" }}>
            <motion.span className="w" initial={{ y: "105%" }} animate={{ y: 0 }}
              transition={{ ...spring, stiffness: 180, damping: 22, delay: 0.05 + n++ * 0.07 }}>
              {w}{wi < words.length - 1 ? " " : ""}
            </motion.span>
          </span>
        ));
        return li === 0 ? <span key={li}>{inner}</span> : <i key={li}>{inner}</i>;
      })}
    </h1>
  );
}
