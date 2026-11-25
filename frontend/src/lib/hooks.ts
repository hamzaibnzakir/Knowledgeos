import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback } from "react";
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
    for (const k of ["notes", "folders", "note", "note-context", "graph", "search", "tags", "system", "favorites"])
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
