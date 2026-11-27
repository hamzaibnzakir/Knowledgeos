// Plain words for audit events, shared by Home, Agents and Activity.
export const ACTION_WORDS: Record<string, string> = {
  "note.create": "Created", "note.update": "Edited", "note.edit": "Edited", "note.append": "Added to",
  "note.move": "Moved", "note.delete": "Deleted", "note.restore": "Restored", "note.relink": "Relinked",
  "memory.remember": "Saved a fact in", "memory.forget": "Retired a fact", "memory.approve": "Approved a fact",
  "memory.propose": "Proposed a change to", "memory.migrate": "Moved memories into Markdown",
  "memory.reject": "Rejected a fact", "memory.proposal": "Decided on a proposal", "memory.purge": "Deleted a fact",
  "note.revise": "Revised a fact in", "note.forget": "Retired a fact in", "note.review": "Reviewed", "note.remember": "Saved a fact in",
  "note.purge": "Deleted for good", "folder.create": "Created a folder", "attachment.upload": "Uploaded",
  "index.rebuild": "Rebuilt the search index", "index.reconcile": "Picked up outside edits",
  "auth.login": "Signed in", "backup.create": "Backed up the vault", "token.create": "Added an agent",
  "token.revoke": "Removed an agent", "oauth.consent": "Connected an agent", "agent.policy": "Changed an agent's access",
};
export const describe = (a: string) => ACTION_WORDS[a] ?? (a.split(".").pop() ?? a).replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());


export function who(actor: string) {
  if (actor === "web") return "you";
  return actor.replace(/^mcp:(agent:)?/, "").replace(/-[0-9a-f]{6}$/, "");
}
