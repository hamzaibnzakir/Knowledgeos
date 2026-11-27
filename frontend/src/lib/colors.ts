// One palette for folders everywhere (graph, home, lists): mid-saturation mineral tones
// that read on both the lichen light theme and the spruce dark theme.
export const FOLDER_COLORS = ["#4f46c8", "#2f8a73", "#c08a2e", "#b4506a", "#3f80ad", "#6f8f3a", "#8754a8", "#b0663f", "#5c6b66"];

// Fixed colours for the standard vault folders so they never collide; others are hashed.
const assigned = new Map<string, string>([
  ["Projects", "#4f46c8"], ["Notes", "#2f8a73"], ["Clients", "#c08a2e"], ["Resources", "#3f80ad"],
  ["Skills", "#6f8f3a"], ["Agents", "#b4506a"], ["Inbox", "#8754a8"], ["Archive", "#5c6b66"], ["Attachments", "#b0663f"],
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
