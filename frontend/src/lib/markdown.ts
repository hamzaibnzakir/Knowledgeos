import MarkdownIt from "markdown-it";
import DOMPurify from "dompurify";
import { fileUrl } from "./api";

// Raw HTML in notes is never rendered (html: false); DOMPurify is a second layer.
const md = new MarkdownIt({ html: false, linkify: true, breaks: false, typographer: true });

type StateInline = Parameters<Parameters<typeof md.inline.ruler.before>[2]>[0];

const IMAGE_EXT = /\.(png|jpe?g|gif|webp|svg|avif)$/i;

function wikiRule(state: StateInline, silent: boolean): boolean {
  const src = state.src;
  let pos = state.pos;
  const embed = src.charCodeAt(pos) === 0x21; /* ! */
  if (embed) pos++;
  if (src.slice(pos, pos + 2) !== "[[") return false;
  const end = src.indexOf("]]", pos + 2);
  if (end < 0) return false;
  const inner = src.slice(pos + 2, end);
  if (!inner || inner.includes("\n") || inner.includes("[")) return false;
  if (!silent) {
    const [targetPart, alias] = inner.split("|");
    const [target, heading] = targetPart.split("#");
    const token = state.push(embed ? "wiki_embed" : "wiki_link", "", 0);
    token.meta = { target: target.trim(), heading: heading?.trim(), alias: alias?.trim() };
  }
  state.pos = end + 2;
  return true;
}

md.inline.ruler.before("link", "wiki", wikiRule);

md.renderer.rules.wiki_link = (tokens, idx) => {
  const { target, heading, alias } = tokens[idx].meta as { target: string; heading?: string; alias?: string };
  const label = alias || (heading && target ? `${target} › ${heading}` : target || heading || "");
  return `<a href="#" class="wikilink" data-target="${md.utils.escapeHtml(target)}">${md.utils.escapeHtml(label)}</a>`;
};

md.renderer.rules.wiki_embed = (tokens, idx) => {
  const { target, alias } = tokens[idx].meta as { target: string; alias?: string };
  const path = target.includes("/") ? target : `Attachments/${target}`;
  if (IMAGE_EXT.test(target)) {
    return `<img class="embed" src="${fileUrl(path)}" alt="${md.utils.escapeHtml(alias || target)}" loading="lazy">`;
  }
  return `<a href="#" class="wikilink embed-link" data-target="${md.utils.escapeHtml(target)}">↳ ${md.utils.escapeHtml(alias || target)}</a>`;
};

// #tags → clickable chips (outside code; markdown-it hands us text tokens only).
const TAG_RE = /(^|[\s(])#([A-Za-z_À-￿][\w/\-À-￿]*)/g;
const defaultText = md.renderer.rules.text!;
md.renderer.rules.text = (tokens, idx, opts, env, self) => {
  const html = defaultText(tokens, idx, opts, env, self);
  return html.replace(TAG_RE, (_m, pre, tag) => `${pre}<a href="#" class="tag" data-tag="${tag}">#${tag}</a>`);
};

// External links open in a new tab without leaking the referrer.
const defaultLinkOpen = md.renderer.rules.link_open ?? ((t, i, o, _e, s) => s.renderToken(t, i, o));
md.renderer.rules.link_open = (tokens, idx, opts, env, self) => {
  const href = String(tokens[idx].attrGet("href") ?? "");
  if (/^https?:/i.test(href)) {
    tokens[idx].attrSet("target", "_blank");
    tokens[idx].attrSet("rel", "noopener noreferrer");
  }
  return defaultLinkOpen(tokens, idx, opts, env, self);
};

function stripFrontmatter(src: string): { fm: string; body: string } {
  const m = /^---[ \t]*\r?\n([\s\S]*?)\r?\n---[ \t]*(\r?\n|$)/.exec(src);
  return m ? { fm: m[1], body: src.slice(m[0].length) } : { fm: "", body: src };
}

export function renderMarkdown(src: string): string {
  const { body } = stripFrontmatter(src);
  let html = md.render(body);
  // GitHub-style task lists (read-only in preview)
  html = html.replace(/<li>\[( |x|X)\] /g, (_m, c) =>
    `<li class="task"><input type="checkbox" disabled${c.trim() ? " checked" : ""}> `);
  return DOMPurify.sanitize(html, { ADD_ATTR: ["target", "data-target", "data-tag"], FORBID_TAGS: ["style", "form"] });
}

export function escapeHtml(s: string): string {
  return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);
}

/** Server snippets mark matches with \x02…\x03; escape everything, then style the markers. */
export function highlightSnippet(s: string): string {
  return escapeHtml(s.replace(/\s+/g, " ")).replace(/\x02/g, "<mark>").replace(/\x03/g, "</mark>");
}

export function renderChatMarkdown(src: string): string {
  // Citations [1] become superscript chips the chat view can wire up.
  const html = renderMarkdown(src);
  return html.replace(/\[(\d{1,2})\](?![^<]*<\/a>)/g, '<sup class="cite" data-n="$1">$1</sup>');
}
