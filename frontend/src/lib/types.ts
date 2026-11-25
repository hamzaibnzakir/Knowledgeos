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

export interface Memory {
  id: number;
  content: string;
  category: string;
  confidence: number;
  source: string;
  status: "active" | "archived";
  pinned: number;
  created_at: string;
  updated_at: string;
  score?: number;
}

export interface Chat {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  message_count: number;
}

export interface Source {
  n: number;
  path?: string;
  title?: string;
  heading?: string;
  label?: string;
  kind: string;
  via?: string;
}

export interface Action {
  tool: string;
  args: Record<string, unknown>;
  status: string;
  pending_id?: string;
  summary?: string;
  path?: string;
  error?: string;
}

export interface Message {
  id: number;
  role: "user" | "assistant";
  content: string;
  created_at: string;
  meta: {
    sources?: Source[];
    actions?: Action[];
    usage?: { input_tokens: number; output_tokens: number; calls: number };
    context_tokens?: number;
    model?: string | null;
  };
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
  stats: Record<string, number>;
  ai: { provider: string; base_url: string; configured: boolean; chat_model: string; agent_model: string; utility_model: string };
  embeddings: { provider: string; available: boolean; pending: number; error: string | null; running: boolean };
  last_backup: string | null;
  mcp_enabled: boolean;
  context_token_budget: number;
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
