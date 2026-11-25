import { useMemo } from "react";
import CodeMirror from "@uiw/react-codemirror";
import { markdown, markdownLanguage } from "@codemirror/lang-markdown";
import { yamlFrontmatter } from "@codemirror/lang-yaml";
import { HighlightStyle, syntaxHighlighting } from "@codemirror/language";
import { tags as t } from "@lezer/highlight";
import { languages } from "@codemirror/language-data";
import { autocompletion, type CompletionContext, type CompletionResult } from "@codemirror/autocomplete";
import { EditorView, keymap } from "@codemirror/view";
import { api, qs } from "../lib/api";

// Calm Markdown highlighting: no underlined headings, muted syntax marks.
const mdStyle = HighlightStyle.define([
  { tag: t.heading1, fontWeight: "700", fontSize: "1.3em" },
  { tag: t.heading2, fontWeight: "700", fontSize: "1.15em" },
  { tag: [t.heading3, t.heading4, t.heading5, t.heading6], fontWeight: "700" },
  { tag: t.strong, fontWeight: "700" },
  { tag: t.emphasis, fontStyle: "italic" },
  { tag: t.strikethrough, textDecoration: "line-through" },
  { tag: [t.link, t.url], color: "var(--accent)" },
  { tag: [t.processingInstruction, t.meta, t.contentSeparator], color: "var(--muted)" },
  { tag: t.monospace, color: "var(--ink-2)", backgroundColor: "var(--bg-2)" },
  { tag: t.quote, color: "var(--ink-2)", fontStyle: "italic" },
  { tag: [t.propertyName, t.definition(t.propertyName)], color: "var(--accent)" },
  { tag: [t.string, t.atom, t.number, t.bool], color: "var(--good)" },
  { tag: t.comment, color: "var(--muted)" },
]);

async function wikiCompletions(ctx: CompletionContext): Promise<CompletionResult | null> {
  const m = ctx.matchBefore(/\[\[[^\]\n|#]*$/);
  if (!m) return null;
  const q = m.text.slice(2);
  const items = await api.get<{ path: string; title: string }[]>(`/api/quick${qs({ q, limit: 20 })}`);
  return {
    from: m.from + 2,
    options: items.map((n) => ({
      label: n.title,
      detail: n.path.includes("/") ? n.path.slice(0, n.path.lastIndexOf("/")) : "",
      apply: (view: EditorView, _c: unknown, from: number, to: number) => {
        const after = view.state.sliceDoc(to, to + 2);
        const insert = n.title + (after === "]]" ? "" : "]]");
        view.dispatch({ changes: { from, to, insert }, selection: { anchor: from + n.title.length + 2 } });
      },
    })),
    validFor: /^[^\]\n|#]*$/,
  };
}

export default function Editor({ value, onChange, onSave }: {
  value: string; onChange: (v: string) => void; onSave: () => void;
}) {
  const extensions = useMemo(() => [
    yamlFrontmatter({ content: markdown({ base: markdownLanguage, codeLanguages: languages }) }),
    syntaxHighlighting(mdStyle),
    EditorView.lineWrapping,
    autocompletion({ override: [wikiCompletions], activateOnTyping: true }),
    keymap.of([{ key: "Mod-s", preventDefault: true, run: () => { onSave(); return true; } }]),
  ], [onSave]);
  return (
    <CodeMirror
      value={value}
      onChange={onChange}
      extensions={extensions}
      theme="none"
      basicSetup={{ lineNumbers: false, foldGutter: false, highlightActiveLine: false, highlightActiveLineGutter: false, autocompletion: false }}
      height="100%"
      autoFocus
    />
  );
}
