export interface NoteSummary {
  path: string;
  title: string;
  folder: string;
  tags: string[];
  aliases: string[];
  updated_at: string;
  created_at?: string;
  word_count: number;
  excerpt: string;
  favorite: boolean;
}

export interface Note {
  path: string;
  content: string;
  hash: string;
  title?: string;
  tags?: string[];
  aliases?: string[];
  properties?: Record<string, unknown>;
  created_at?: string;
  updated_at?: string;
  word_count?: number;
  favorite: boolean;
}

export interface NoteContext {
  backlinks: { path: string; title: string }[];
  outgoing: { target_raw: string; target_path: string | null; kind: string }[];
  related: { path: string; title: string; score: number; reasons: string[] }[];
  unlinked_mentions: { path: string; snippet: string }[];
  versions: number;
}

export interface SearchHit {
  path: string;
  title: string;
  folder: string;
  tags: string[];
  updated_at: string;
  snippet: string;
  score: number;
  match: "keyword" | "semantic" | "keyword+semantic";
}

export interface Folder {
  name: string;
  path: string;
  children: Folder[];
}

export interface Fact {
  id: string;
  path: string;
  subject: string;
  kind: "profile" | "rules" | "project" | "client" | "person" | "topic";
  text: string;
  by_agent: string;
  on_date: string;
  src: string;
  external: boolean;
  review: "new" | "ok";
  flag: string;
  status: "current" | "superseded";
  until: string;
  replaced_by: string;
  reason: string;
  score?: number;
}

export interface Subject {
  path: string;
  title: string;
  subject: string;
  kind: Fact["kind"];
  facts: number;
  unreviewed: number;
  updated_at: string;
  aliases: string[];
}

export interface Proposal {
  id: string;
  path: string;
  op: "add" | "replace" | "retire";
  fact_id: string;
  text: string;
  agent: string;
  src: string;
  external: boolean;
  flag: string;
  reason: string;
  created_at: string;
  current_text?: string | null;
}

export interface MemoryHealth {
  facts: number;
  unreviewed: number;
  flagged: Fact[];
  duplicates: { a: Fact; b: Fact; similarity: number }[];
  stale: Fact[];
  stale_days: number;
  core: Record<string, { chars: number; limit: number }>;
  proposals: number;
}

export interface RememberResult {
  status: "saved" | "proposed" | "exists" | "needs_decision" | "skipped";
  fact_id?: string;
  similar?: Fact[];
  fact?: Fact;
  message?: string;
  warning?: string;
}

export interface GraphNode {
  id: string;
  type: "note" | "tag" | "missing";
  label: string;
  folder?: string;
  tags?: string[];
  degree: number;
  words?: number;
}

export interface GraphData {
  nodes: GraphNode[];
  edges: { source: string; target: string; type: string }[];
  center?: string;
}

export interface AuditEvent {
  id: number;
  chat_id: string | null;
  action: string;
  status: string;
  details: Record<string, unknown>;
  actor: string;
  created_at: string;
}

export interface Credential {
  client_id: string;
  kind: "env" | "token" | "oauth";
  label: string;
  scopes: string[];
  created_at?: string;
  last_used_at?: string | null;
  revoked_at?: string | null;
  revocable: boolean;
  redirect_uris?: string[];
  active_tokens?: number;
  activity: { first_connected?: string; last_seen?: string; actions?: number; retrievals?: number; writes?: number };
}

export interface SystemInfo {
  version: string;
  owner_name?: string;
  stats: Record<string, number>;
  embeddings: { provider: string; available: boolean; pending: number; error: string | null; running: boolean };
  last_backup: string | null;
  mcp_enabled: boolean;
  context_token_budget: number;
  memory: { brief_token_budget: number; core_max_chars: number; stale_days: number; write_token_name: string };
  limits: Record<string, number>;
}

export interface Version {
  id: number;
  path: string;
  content_hash: string;
  actor: string;
  reason: string;
  created_at: string;
}
