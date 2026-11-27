// One palette for folders everywhere (islands, graph, lists). Saturated enough to read on white tiles
// and on the dark theme's navy.
export const FOLDER_COLORS = ["#2e4bff", "#ff6a4d", "#12a57a", "#8a5cff", "#e0a100", "#0e9fb8", "#d9468f", "#5d7a1f", "#9aa0b5"];

// Fixed colours for the standard vault folders so they never collide; others are hashed.
const assigned = new Map<string, string>([
  ["Projects", "#2e4bff"], ["Clients", "#ff6a4d"], ["Notes", "#12a57a"], ["Resources", "#8a5cff"],
  ["Skills", "#e0a100"], ["Inbox", "#9aa0b5"], ["Agents", "#d9468f"], ["Memory", "#0e9fb8"], ["Archive", "#7c8197"], ["Attachments", "#5d7a1f"],
]);

export function folderColor(folder: string): string {
  const top = (folder || "").split("/")[0] || "(root)";
  if (!assigned.has(top)) {
    let h = 0;
    for (const c of top) h = (h * 31 + c.charCodeAt(0)) >>> 0;
    assigned.set(top, FOLDER_COLORS[h % FOLDER_COLORS.length]);
  }
  return assigned.get(top)!;
}
