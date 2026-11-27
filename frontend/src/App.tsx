import { lazy, Suspense, useEffect, useState } from "react";
import { Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Menu, Search as SearchIcon } from "lucide-react";
import { api, setUnauthorizedHandler } from "./lib/api";
import Sidebar from "./components/Sidebar";
import CommandPalette from "./components/CommandPalette";
import Login from "./pages/Login";
import Home from "./pages/Home";
const SettingsPage = lazy(() => import("./pages/Settings"));
const ActivityPage = lazy(() => import("./pages/Activity"));
const AgentsPage = lazy(() => import("./pages/Agents"));
const MemoryPage = lazy(() => import("./pages/Memory"));
const ReviewPage = lazy(() => import("./pages/Review"));
const GraphPage = lazy(() => import("./pages/Graph"));
const SearchPage = lazy(() => import("./pages/Search"));
const NotePage = lazy(() => import("./pages/NotePage"));
import { useTheme } from "./components/ui";
import { useCreateNote } from "./lib/hooks";

type AuthStatus = { authenticated: boolean; password_configured: boolean };

export default function App() {
  useTheme();
  const qc = useQueryClient();
  const status = useQuery({ queryKey: ["auth"], queryFn: () => api.get<AuthStatus>("/api/auth/status"), staleTime: 60_000 });

  useEffect(() => {
    setUnauthorizedHandler(() => qc.setQueryData(["auth"], (s: AuthStatus | undefined) => ({ ...(s ?? { password_configured: true }), authenticated: false })));
  }, [qc]);

  if (status.isLoading) return <div className="empty">Loading…</div>;
  if (status.isError) return <div className="empty">Cannot reach the server. Is Knowledge OS running?</div>;
  if (!status.data?.authenticated) return <Login configured={status.data?.password_configured ?? true} />;
  return <Shell />;
}

function Shell() {
  const [drawer, setDrawer] = useState(false);
  const [palette, setPalette] = useState(false);
  const loc = useLocation();
  const nav = useNavigate();
  const createNote = useCreateNote();

  useEffect(() => setDrawer(false), [loc.pathname]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const mod = e.metaKey || e.ctrlKey;
      if (mod && e.key.toLowerCase() === "k") { e.preventDefault(); setPalette((p) => !p); }
      else if (mod && e.shiftKey && e.key.toLowerCase() === "f") { e.preventDefault(); nav("/search"); }
      else if (e.altKey && e.key.toLowerCase() === "n") { e.preventDefault(); createNote(); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [nav, createNote]);

  const title = loc.pathname.startsWith("/note/") ? decodeURIComponent(loc.pathname.split("/").pop() ?? "").replace(/\.md$/, "")
    : ({ "/": "Home", "/search": "Search", "/graph": "Graph", "/memory": "Memory", "/review": "Review", "/agents": "Agents & MCP", "/activity": "Activity", "/settings": "Settings" } as Record<string, string>)[loc.pathname]
    ?? "Knowledge OS";

  return (
    <div className={`shell ${drawer ? "drawer" : ""}`} onClick={(e) => { if (drawer && e.target === e.currentTarget) setDrawer(false); }}>
      <Sidebar onPalette={() => setPalette(true)} onClose={() => setDrawer(false)} />
      <div className="main">
        <div className="mobilebar">
          <button className="btn ghost icon" aria-label="Menu" onClick={() => setDrawer(true)}><Menu /></button>
          <b>{title}</b>
          <button className="btn ghost icon" aria-label="Search" onClick={() => setPalette(true)}><SearchIcon /></button>
        </div>
        <Suspense fallback={<div className="empty">Loading…</div>}>
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/note/*" element={<NotePage />} />
          <Route path="/search" element={<SearchPage />} />
          <Route path="/graph" element={<GraphPage />} />
          <Route path="/review" element={<ReviewPage />} />
          <Route path="/memory" element={<MemoryPage />} />
          <Route path="/agents" element={<AgentsPage />} />
          <Route path="/activity" element={<ActivityPage />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
        </Suspense>
      </div>
      {palette && <CommandPalette onClose={() => setPalette(false)} />}
    </div>
  );
}
