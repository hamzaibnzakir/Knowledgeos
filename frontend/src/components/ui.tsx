import { createContext, useCallback, useContext, useEffect, useRef, useState, useSyncExternalStore, type ReactNode } from "react";

// ---------- toasts
type Toast = { id: number; text: string; kind: "info" | "error"; action?: { label: string; run: () => void } };
const ToastCtx = createContext<(text: string, opts?: { kind?: "info" | "error"; action?: Toast["action"] }) => void>(() => {});

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<Toast[]>([]);
  const seq = useRef(0);
  const push = useCallback((text: string, opts: { kind?: "info" | "error"; action?: Toast["action"] } = {}) => {
    const id = ++seq.current;
    setItems((x) => [...x.slice(-3), { id, text, kind: opts.kind ?? "info", action: opts.action }]);
    setTimeout(() => setItems((x) => x.filter((t) => t.id !== id)), opts.action ? 8000 : 3800);
  }, []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="toasts" role="status" aria-live="polite">
        {items.map((t) => (
          <div key={t.id} className={`toast ${t.kind === "error" ? "error" : ""}`}>
            <span className="grow">{t.text}</span>
            {t.action && <button onClick={t.action.run}>{t.action.label}</button>}
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}
export const useToast = () => useContext(ToastCtx);

export function errorText(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

// ---------- modal
export function Modal({ title, onClose, children, footer, wide }: {
  title?: ReactNode; onClose: () => void; children: ReactNode; footer?: ReactNode; wide?: boolean;
}) {
  useEffect(() => {
    const k = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [onClose]);
  return (
    <div className="overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal" role="dialog" aria-modal="true" style={wide ? { width: "min(760px,100%)" } : undefined}>
        {title && <div className="modal-head">{title}</div>}
        <div className="modal-body">{children}</div>
        {footer && <div className="modal-foot">{footer}</div>}
      </div>
    </div>
  );
}

// ---------- prompt / confirm dialogs (promise based)
type DialogState =
  | { kind: "prompt"; title: string; label?: string; value: string; placeholder?: string; resolve: (v: string | null) => void }
  | { kind: "confirm"; title: string; body?: string; danger?: boolean; confirm?: string; resolve: (v: boolean) => void };
const DialogCtx = createContext<{
  prompt: (title: string, opts?: { label?: string; value?: string; placeholder?: string }) => Promise<string | null>;
  confirm: (title: string, opts?: { body?: string; danger?: boolean; confirm?: string }) => Promise<boolean>;
}>({ prompt: async () => null, confirm: async () => false });

export function DialogProvider({ children }: { children: ReactNode }) {
  const [d, setD] = useState<DialogState | null>(null);
  const [val, setVal] = useState("");
  const prompt = useCallback((title: string, opts: { label?: string; value?: string; placeholder?: string } = {}) =>
    new Promise<string | null>((resolve) => { setVal(opts.value ?? ""); setD({ kind: "prompt", title, ...opts, value: opts.value ?? "", resolve }); }), []);
  const confirm = useCallback((title: string, opts: { body?: string; danger?: boolean; confirm?: string } = {}) =>
    new Promise<boolean>((resolve) => setD({ kind: "confirm", title, ...opts, resolve })), []);
  const close = (v: string | boolean | null) => {
    if (!d) return;
    if (d.kind === "prompt") d.resolve(typeof v === "string" ? v : null);
    else d.resolve(v === true);
    setD(null);
  };
  return (
    <DialogCtx.Provider value={{ prompt, confirm }}>
      {children}
      {d && (
        <Modal title={d.title} onClose={() => close(d.kind === "prompt" ? null : false)} footer={
          <>
            <button className="btn" onClick={() => close(d.kind === "prompt" ? null : false)}>Cancel</button>
            {d.kind === "prompt" ? (
              <button className="btn primary" disabled={!val.trim()} onClick={() => close(val.trim())}>OK</button>
            ) : (
              <button className={`btn ${d.danger ? "danger" : "primary"}`} autoFocus onClick={() => close(true)}>{d.confirm ?? "Confirm"}</button>
            )}
          </>
        }>
          {d.kind === "prompt" ? (
            <form onSubmit={(e) => { e.preventDefault(); if (val.trim()) close(val.trim()); }}>
              {d.label && <label className="small muted">{d.label}</label>}
              <input className="input" autoFocus value={val} placeholder={d.placeholder} onChange={(e) => setVal(e.target.value)} />
            </form>
          ) : d.body ? <p className="muted" style={{ margin: 0 }}>{d.body}</p> : null}
        </Modal>
      )}
    </DialogCtx.Provider>
  );
}
export const useDialog = () => useContext(DialogCtx);

// ---------- misc
export function timeAgo(iso?: string | null): string {
  if (!iso) return "";
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return "";
  const s = Math.max(0, (Date.now() - t) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  if (s < 86400 * 7) return `${Math.floor(s / 86400)}d ago`;
  return new Date(iso).toLocaleDateString();
}

export function bytes(n?: number | null): string {
  if (!n && n !== 0) return "";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

export function useDebounced<T>(value: T, ms = 250): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}

// Theme lives in one module-level store so every component sees the same value.
let themeValue = (() => { try { return localStorage.getItem("kos-theme") ?? "system"; } catch { return "system"; } })();
const themeListeners = new Set<() => void>();
function applyTheme() {
  const dark = themeValue === "dark" || (themeValue === "system" && window.matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.dataset.theme = dark ? "dark" : "light";
}
applyTheme();
window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", applyTheme);

export function useTheme(): [string, (t: string) => void] {
  const theme = useSyncExternalStore((cb) => { themeListeners.add(cb); return () => themeListeners.delete(cb); }, () => themeValue);
  const set = useCallback((t: string) => {
    themeValue = t;
    try { localStorage.setItem("kos-theme", t); } catch { /* storage unavailable */ }
    applyTheme();
    themeListeners.forEach((l) => l());
  }, []);
  return [theme, set];
}
