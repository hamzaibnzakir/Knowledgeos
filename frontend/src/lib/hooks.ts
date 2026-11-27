import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, noteHref } from "./api";
import type { Folder, NoteSummary, SystemInfo } from "./types";
import { errorText, useDialog, useToast } from "../components/ui";

export const useNotes = () =>
  useQuery({ queryKey: ["notes"], queryFn: () => api.get<NoteSummary[]>("/api/notes?limit=5000") });

export const useFolders = () => useQuery({ queryKey: ["folders"], queryFn: () => api.get<Folder>("/api/folders") });

export const useSystem = () =>
  useQuery({ queryKey: ["system"], queryFn: () => api.get<SystemInfo>("/api/system"), refetchInterval: 30_000 });

/** Invalidate everything derived from the vault after a write. */
export function useVaultRefresh() {
  const qc = useQueryClient();
  return useCallback(() => {
    for (const k of ["notes", "folders", "note", "note-context", "graph", "search", "tags", "system", "favorites", "memory"])
      qc.invalidateQueries({ queryKey: [k] });
  }, [qc]);
}

export function useCreateNote() {
  const nav = useNavigate();
  const toast = useToast();
  const { prompt } = useDialog();
  const refresh = useVaultRefresh();
  return useCallback(
    async (folder = "Notes", initialTitle = "") => {
      const name = initialTitle || (await prompt("New note", { label: `Title (in ${folder || "vault root"})`, placeholder: "Untitled" }));
      if (!name) return;
      const clean = name.replace(/[\\:*?"<>|]/g, "-").trim();
      const path = name.includes("/") ? clean : `${folder ? folder + "/" : ""}${clean}`;
      const title = clean.split("/").pop();
      try {
        const r = await api.post<{ path: string }>("/api/notes", { path, content: `# ${title}\n\n` });
        refresh();
        nav(noteHref(r.path) + "?edit=1");
      } catch (e) {
        toast(errorText(e), { kind: "error" });
      }
    },
    [nav, prompt, refresh, toast],
  );
}

export function useOpenDaily() {
  const nav = useNavigate();
  const refresh = useVaultRefresh();
  return useCallback(async () => {
    const r = await api.post<{ path: string }>("/api/notes/daily");
    refresh();
    nav(noteHref(r.path) + "?edit=1");
  }, [nav, refresh]);
}

/** Refresh everything that shows memory (facts, subjects, review queue, badge counts). */
export function useMemoryRefresh() {
  const qc = useQueryClient();
  return useCallback(() => {
    for (const k of ["memory", "system", "notes", "note", "audit", "agents"]) qc.invalidateQueries({ queryKey: [k] });
  }, [qc]);
}

/** True on touch-first devices (phones, tablets), where swipes beat small buttons. */
export function useCoarsePointer(): boolean {
  const q = "(pointer: coarse)";
  const [v, setV] = useState(() => typeof window !== "undefined" && window.matchMedia(q).matches);
  useEffect(() => {
    const m = window.matchMedia(q);
    const on = () => setV(m.matches);
    m.addEventListener("change", on);
    return () => m.removeEventListener("change", on);
  }, []);
  return v;
}

/** True when the screen is phone-sized. */
export function usePhone(): boolean {
  const q = "(max-width: 820px)";
  const [v, setV] = useState(() => typeof window !== "undefined" && window.matchMedia(q).matches);
  useEffect(() => {
    const m = window.matchMedia(q);
    const on = () => setV(m.matches);
    m.addEventListener("change", on);
    return () => m.removeEventListener("change", on);
  }, []);
  return v;
}
