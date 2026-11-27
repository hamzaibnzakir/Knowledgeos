"""Deterministic Markdown parsing: front matter, tags, links, headings, chunks.

Compatible with Obsidian conventions: YAML front matter (including list
values), `aliases`, `tags`, inline `#tags`, `[[Wiki Links]]` with `#heading`,
`^block` and `|alias` parts, `![[embeds]]` and relative Markdown links.
"""
from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any
from urllib.parse import unquote

import yaml

from .util import estimate_tokens, sha256

FRONTMATTER_RE = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.S)
FENCE_RE = re.compile(r"^(```|~~~).*?^\1[ \t]*$", re.S | re.M)
INLINE_CODE_RE = re.compile(r"`[^`\n]*`")
PROTECT_RE = re.compile(FENCE_RE.pattern + "|" + INLINE_CODE_RE.pattern, re.S | re.M)
WIKI_RE = re.compile(r"(!?)\[\[([^\[\]\|#\^\n]*)(#[^\[\]\|\n]*)?(?:\|([^\[\]\n]*))?\]\]")
MDLINK_RE = re.compile(r"(!?)\[([^\]\n]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
TAG_RE = re.compile(r"(?:^|(?<=[\s(\[,;]))#([A-Za-z_À-￿][\w/\-À-￿]*)")
HEADING_RE = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$", re.M)
URL_RE = re.compile(r"https?://\S+")

CHUNK_CHARS = 1400
CHUNK_OVERLAP = 200


@dataclass
class Link:
    target: str          # as written, without heading/alias
    key: str             # normalised lookup key
    kind: str            # wiki | embed | md
    heading: str = ""


@dataclass
class Chunk:
    ord: int
    heading: str
    text: str
    hash: str
    tokens: int


@dataclass
class ParsedNote:
    title: str
    frontmatter: dict[str, Any]
    body: str
    tags: list[str]
    aliases: list[str]
    properties: dict[str, Any]
    links: list[Link]
    headings: list[dict]
    word_count: int
    excerpt: str
    chunks: list[Chunk] = field(default_factory=list)


def _jsonable(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def split_frontmatter(content: str) -> tuple[dict[str, Any], str, str]:
    """Return (frontmatter, body, raw_frontmatter_block)."""
    m = FRONTMATTER_RE.match(content)
    if not m:
        return {}, content, ""
    try:
        data = yaml.safe_load(m.group(1)) or {}
        if not isinstance(data, dict):
            data = {}
    except yaml.YAMLError:
        data = {}
    return _jsonable(data), content[m.end():], m.group(0)


def _as_list(value: Any) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, str):
        return [p for p in re.split(r"[,\s]+", value) if p]
    if isinstance(value, list):
        return [str(v) for v in value if v not in (None, "")]
    return [str(value)]


def normalize_tag(tag: str) -> str:
    return tag.strip().lstrip("#").strip("/").lower()


def link_key(target: str) -> str:
    """Normalise a link target to the key used for resolution.

    `Projects/Meta Ads.md` -> `projects/meta ads`; `Meta Ads` -> `meta ads`.
    """
    t = unquote(target).strip().replace("\\", "/").lstrip("./")
    if t.lower().endswith(".md"):
        t = t[:-3]
    return re.sub(r"\s+", " ", t).strip().lower()


def strip_code(text: str) -> str:
    text = FENCE_RE.sub(lambda m: "\n" * m.group(0).count("\n"), text)
    return INLINE_CODE_RE.sub(" ", text)


def extract_links(body: str, source_path: str = "") -> list[Link]:
    text = strip_code(body)
    seen: set[tuple[str, str]] = set()
    out: list[Link] = []
    for bang, target, heading, _alias in WIKI_RE.findall(text):
        target = target.strip()
        if not target:  # [[#Heading]] refers to the same note
            continue
        kind = "embed" if bang else "wiki"
        key = link_key(target)
        if (key, kind) not in seen:
            seen.add((key, kind))
            out.append(Link(target=target, key=key, kind=kind, heading=(heading or "").lstrip("#")))
    base_dir = posixpath.dirname(source_path)
    for bang, _label, href in MDLINK_RE.findall(text):
        if re.match(r"^[a-z][a-z0-9+.-]*:", href, re.I) or href.startswith("#"):
            continue
        href = href.split("#", 1)[0]
        if not href.lower().endswith(".md"):
            continue
        resolved = posixpath.normpath(posixpath.join(base_dir, unquote(href))) if not href.startswith("/") else href.lstrip("/")
        if resolved.startswith(".."):
            continue
        key = link_key(resolved)
        if (key, "md") not in seen:
            seen.add((key, "md"))
            out.append(Link(target=resolved, key=key, kind="md"))
    return out


def extract_inline_tags(body: str) -> list[str]:
    text = strip_code(body)
    text = URL_RE.sub(" ", text)
    text = WIKI_RE.sub(" ", text)
    # Headings start with "# " (space) so they never match TAG_RE.
    tags = {normalize_tag(t) for t in TAG_RE.findall(text)}
    return sorted(t for t in tags if t and not t.isdigit())


def extract_headings(body: str) -> list[dict]:
    text = strip_code(body)
    return [{"level": len(h), "text": t.strip()} for h, t in HEADING_RE.findall(text)]


def _split_long(text: str) -> list[str]:
    if len(text) <= CHUNK_CHARS:
        return [text]
    parts: list[str] = []
    paragraphs = re.split(r"\n\s*\n", text)
    buf = ""
    for para in paragraphs:
        if len(para) > CHUNK_CHARS:
            if buf:
                parts.append(buf)
                buf = ""
            step = CHUNK_CHARS - CHUNK_OVERLAP
            for i in range(0, len(para), step):
                parts.append(para[i:i + CHUNK_CHARS])
            continue
        if len(buf) + len(para) + 2 > CHUNK_CHARS and buf:
            parts.append(buf)
            tail = buf[-CHUNK_OVERLAP:]
            buf = (tail + "\n\n" + para) if len(tail) + len(para) < CHUNK_CHARS else para
        else:
            buf = f"{buf}\n\n{para}" if buf else para
    if buf:
        parts.append(buf)
    return parts


def chunk_body(title: str, body: str) -> list[Chunk]:
    """Split by headings, then by size. Each chunk remembers its heading path."""
    sections: list[tuple[str, str]] = []
    current_heading = ""
    stack: list[tuple[int, str]] = []
    buf: list[str] = []
    in_fence = False
    for line in body.splitlines():
        if line.lstrip().startswith(("```", "~~~")):
            in_fence = not in_fence
        m = None if in_fence else HEADING_RE.match(line)
        if m:
            if "".join(buf).strip():
                sections.append((current_heading, "\n".join(buf).strip()))
            buf = []
            level = len(m.group(1))
            stack = [s for s in stack if s[0] < level] + [(level, m.group(2).strip())]
            current_heading = " › ".join(s[1] for s in stack)
            continue
        buf.append(line)
    if "".join(buf).strip():
        sections.append((current_heading, "\n".join(buf).strip()))
    if not sections and title:
        sections = [("", title)]

    chunks: list[Chunk] = []
    for heading, text in sections:
        for piece in _split_long(text):
            piece = piece.strip()
            if not piece:
                continue
            chunks.append(Chunk(ord=len(chunks), heading=heading, text=piece,
                                hash=sha256(f"{title}\x00{heading}\x00{piece}"), tokens=estimate_tokens(piece)))
    return chunks


def make_excerpt(body: str, limit: int = 220) -> str:
    text = FENCE_RE.sub(" ", body)
    text = re.sub(r"\[[\w-]+::[^\]\n]*\]", "", text)          # Dataview inline fields (memory facts)
    text = re.sub(r"\s\^[A-Za-z0-9][\w-]{1,31}(?=\s|$)", "", text)  # block ids
    text = HEADING_RE.sub("", text)
    text = WIKI_RE.sub(lambda m: (m.group(4) or m.group(2)), text)
    text = MDLINK_RE.sub(lambda m: m.group(2), text)
    text = re.sub(r"^\s*(?:[-*+]|\d+\.)\s+(?:\[[ xX]\]\s*)?", "", text, flags=re.M)
    text = re.sub(r"^\s*>\s?", "", text, flags=re.M)
    text = re.sub(r"(\*\*|__|`|~~)", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit] + ("…" if len(text) > limit else "")


def parse_note(path: str, content: str) -> ParsedNote:
    fm, body, _ = split_frontmatter(content)
    stem = posixpath.splitext(posixpath.basename(path))[0]
    title = str(fm.get("title") or "").strip() or stem
    aliases = _as_list(fm.get("aliases", fm.get("alias")))
    tags = {normalize_tag(t) for t in _as_list(fm.get("tags", fm.get("tag")))}
    tags.update(extract_inline_tags(body))
    tags.discard("")
    props = {k: v for k, v in fm.items() if k not in {"title", "aliases", "alias", "tags", "tag"}}
    words = len(re.findall(r"\w+", strip_code(body)))
    parsed = ParsedNote(
        title=title,
        frontmatter=fm,
        body=body,
        tags=sorted(tags),
        aliases=aliases,
        properties=props,
        links=extract_links(body, path),
        headings=extract_headings(body),
        word_count=words,
        excerpt=make_excerpt(body),
    )
    parsed.chunks = chunk_body(title, body)
    return parsed


def rewrite_wiki_links(content: str, old_keys: set[str], new_target: str) -> tuple[str, int]:
    """Point every [[link]] whose key is in old_keys at new_target. Returns (content, count)."""
    count = 0

    def repl(m: re.Match) -> str:
        nonlocal count
        bang, target, heading, alias = m.group(1), m.group(2), m.group(3) or "", m.group(4)
        if link_key(target) not in old_keys:
            return m.group(0)
        count += 1
        return f"{bang}[[{new_target}{heading}{'|' + alias if alias is not None else ''}]]"

    # Do not touch code: rewrite only outside fenced blocks and inline code.
    out, last = [], 0
    for fm in PROTECT_RE.finditer(content):
        out.append(WIKI_RE.sub(repl, content[last:fm.start()]))
        out.append(fm.group(0))
        last = fm.end()
    out.append(WIKI_RE.sub(repl, content[last:]))
    return "".join(out), count


def set_frontmatter(content: str, updates: dict[str, Any]) -> str:
    fm, body, raw = split_frontmatter(content)
    fm.update({k: v for k, v in updates.items() if v is not None})
    for k, v in updates.items():
        if v is None:
            fm.pop(k, None)
    if not fm:
        return body
    block = yaml.safe_dump(fm, sort_keys=False, allow_unicode=True).strip()
    return f"---\n{block}\n---\n\n{body.lstrip()}"
