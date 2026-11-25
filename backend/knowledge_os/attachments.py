"""Uploaded files and web links: stored, text-extracted, and only filed into the
vault when the user (or an agent) explicitly asks for it."""
from __future__ import annotations

import hashlib
import html
import ipaddress
import mimetypes
import posixpath
import re
import shutil
import socket
import subprocess
import uuid
import zipfile
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx

from . import db, vault
from .config import get_settings
from .util import KOSError, NotFound, now, record_event, slugify

TEXT_TYPES = {".md", ".txt", ".csv", ".json", ".yaml", ".yml", ".html", ".htm", ".xml", ".py", ".js", ".ts",
              ".tsx", ".jsx", ".css", ".sql", ".sh", ".toml", ".ini", ".log", ".rst"}
MAX_EXTRACT = 400_000


def _safe_name(name: str) -> str:
    name = Path(name or "attachment").name
    name = re.sub(r"[^\w.\- ]+", "_", name).strip(" .") or "attachment"
    return name[:120]


def extract_text(path: Path, mime: str) -> tuple[str, str]:
    """Return (text, method). Never raises."""
    suffix = path.suffix.lower()
    try:
        if mime == "application/pdf" or suffix == ".pdf":
            if shutil.which("pdftotext"):
                r = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, timeout=90)
                return r.stdout.strip()[:MAX_EXTRACT], "pdftotext"
            return "", "pdftotext unavailable"
        if mime.startswith("image/"):
            if shutil.which("tesseract"):
                r = subprocess.run(["tesseract", str(path), "stdout"], capture_output=True, text=True, timeout=90)
                return r.stdout.strip()[:MAX_EXTRACT], "ocr"
            return "", "ocr unavailable"
        if suffix == ".docx":
            with zipfile.ZipFile(path) as z:
                xml = z.read("word/document.xml").decode("utf-8", "ignore")
            xml = re.sub(r"</w:p>", "\n\n", xml)
            return html.unescape(re.sub(r"<[^>]+>", "", xml)).strip()[:MAX_EXTRACT], "docx"
        if suffix in (".html", ".htm"):
            return html_to_text(path.read_text("utf-8", errors="ignore"))[1][:MAX_EXTRACT], "html"
        if suffix in TEXT_TYPES or mime.startswith("text/"):
            return path.read_text("utf-8", errors="ignore")[:MAX_EXTRACT], "text"
    except Exception as e:  # extraction is best effort
        return "", f"failed: {type(e).__name__}"
    return "", "unsupported type"


async def save_upload(upload, chat_id: str | None, actor: str = "web") -> dict:
    s = get_settings()
    limit = s.max_upload_mb * 1024 * 1024
    aid = uuid.uuid4().hex
    name = _safe_name(upload.filename or "attachment")
    dest = s.attachments_dir / f"{aid}-{name}"
    h = hashlib.sha256()
    size = 0
    with dest.open("wb") as f:
        while chunk := await upload.read(1024 * 1024):
            size += len(chunk)
            if size > limit:
                f.close()
                dest.unlink(missing_ok=True)
                raise KOSError(f"File is larger than the {s.max_upload_mb} MB limit", status=413)
            h.update(chunk)
            f.write(chunk)
    mime = upload.content_type or mimetypes.guess_type(name)[0] or "application/octet-stream"
    text, method = extract_text(dest, mime)
    with db.connect() as conn:
        conn.execute("INSERT INTO attachments(id,chat_id,filename,mime_type,path,extracted_text,created_at,size,sha256)"
                     " VALUES(?,?,?,?,?,?,?,?,?)",
                     (aid, chat_id, name, mime, dest.name, text, now(), size, h.hexdigest()))
        record_event(conn, "attachment.upload", details={"attachment_id": aid, "filename": name, "bytes": size,
                                                         "extraction": method}, actor=actor, chat_id=chat_id)
    return {"id": aid, "filename": name, "mime_type": mime, "size": size, "extraction": method,
            "extracted_chars": len(text), "preview": text[:1500]}


def get(aid: str) -> dict:
    with db.connect() as conn:
        r = conn.execute("SELECT * FROM attachments WHERE id=?", (aid,)).fetchone()
    if not r:
        raise NotFound("Attachment not found")
    return dict(r)


def list_for_chat(chat_id: str) -> list[dict]:
    with db.connect() as conn:
        rows = conn.execute("SELECT id,filename,mime_type,size,note_path,created_at,length(extracted_text) AS chars "
                            "FROM attachments WHERE chat_id=? ORDER BY created_at", (chat_id,)).fetchall()
    return [dict(r) for r in rows]


def file_path(a: dict) -> Path:
    stored = Path(a["path"] or "")
    local = get_settings().attachments_dir / stored.name
    # Legacy rows stored absolute host paths; after a move (e.g. into Docker) look in the attachments dir.
    if stored.is_absolute() and stored.is_file() and not local.is_file():
        return stored
    return local


def delete(aid: str) -> None:
    a = get(aid)
    p = file_path(a)
    if p.is_file() and p.resolve().is_relative_to(get_settings().attachments_dir.resolve()):
        p.unlink(missing_ok=True)
    with db.connect() as conn:
        conn.execute("DELETE FROM attachments WHERE id=?", (aid,))


def save_to_vault(aid: str, destination: str, actor: str = "web", *, as_skill: bool = False) -> dict:
    """File an attachment into the vault: original file under Attachments/ plus a Markdown note with its text."""
    a = get(aid)
    stem = posixpath.splitext(a["filename"])[0]
    if as_skill:
        destination = f"Skills/{stem}"
    dest_note = vault.normalize_rel(destination or f"Inbox/{stem}")
    src = file_path(a)
    asset_rel = None
    if src.is_file() and not a["filename"].lower().endswith((".md", ".txt")):
        asset_rel = vault.save_asset(f"Attachments/{a['filename']}", src.read_bytes(), actor)
    title = posixpath.splitext(posixpath.basename(dest_note))[0]
    parts = [f"# {title}", ""]
    if asset_rel:
        embed = f"![[{asset_rel}]]" if a["mime_type"].startswith("image/") else f"[[{asset_rel}]]"
        parts += [f"Source file: {embed}", ""]
    parts.append(a["extracted_text"] or "_No text could be extracted from this file._")
    content = "\n".join(parts).rstrip() + "\n"
    if as_skill:
        from .tools import _skill_content
        content = _skill_content(title, content)
    res = vault.create(dest_note, content, actor, chat_id=a["chat_id"])
    with db.connect() as conn:
        conn.execute("UPDATE attachments SET note_path=? WHERE id=?", (res["path"], aid))
    return {"path": res["path"], "asset": asset_rel}


# --------------------------------------------------------------------------- links

class _TextExtractor(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "nav", "footer", "header", "form", "iframe"}
    BLOCK = {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section", "article", "pre", "blockquote"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title = ""
        self._skip = 0
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip += 1
        if tag == "title":
            self._in_title = True
        if tag in self.BLOCK:
            self.parts.append("\n")
        if tag in ("h1", "h2", "h3"):
            self.parts.append("#" * int(tag[1]) + " ")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip:
            self._skip -= 1
        if tag == "title":
            self._in_title = False
        if tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self._skip:
            self.parts.append(data)


def html_to_text(raw: str) -> tuple[str, str]:
    p = _TextExtractor()
    p.feed(raw)
    text = "".join(p.parts)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n\s*(\n\s*)+", "\n\n", text)
    return p.title.strip(), text.strip()


def _check_public(url: str) -> None:
    s = get_settings()
    u = urlparse(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise KOSError("Only http(s) URLs can be imported")
    if s.allow_private_urls:
        return
    try:
        infos = socket.getaddrinfo(u.hostname, u.port or (443 if u.scheme == "https" else 80))
    except socket.gaierror as e:
        raise KOSError("Could not resolve that host") from e
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            raise KOSError("That URL points to a private or local address and was blocked")


def fetch_url(url: str) -> tuple[str, str, str]:
    """Fetch a public web page safely. Returns (final_url, title, text)."""
    current = url.strip()
    with httpx.Client(timeout=20, follow_redirects=False, headers={"User-Agent": "KnowledgeOS/2.0 (+private)"}) as client:
        for _ in range(5):
            _check_public(current)
            r = client.get(current)
            if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
                current = urljoin(current, r.headers["location"])
                continue
            break
        else:
            raise KOSError("Too many redirects")
    if r.status_code >= 400:
        raise KOSError(f"The page returned HTTP {r.status_code}")
    ctype = r.headers.get("content-type", "")
    if not any(t in ctype for t in ("text/html", "text/plain", "application/xhtml", "text/markdown")):
        raise KOSError("Only HTML and text pages can be imported")
    raw = r.content[:3_000_000].decode(r.encoding or "utf-8", errors="ignore")
    if "html" in ctype:
        title, text = html_to_text(raw)
    else:
        title, text = "", raw
    return current, title, text[:MAX_EXTRACT]


def ingest_link(url: str, *, folder: str = "Resources", chat_id: str | None = None, actor: str = "web") -> dict:
    final_url, title, text = fetch_url(url)
    title = re.sub(r"\s+", " ", title).strip()[:100] or (urlparse(final_url).path.rstrip("/").split("/")[-1] or urlparse(final_url).hostname or "Link")
    safe_title = re.sub(r'[\x00-\x1f<>:"|?*/\\#\[\]]+', " ", title).strip(" .") or "Link"
    rel = f"{folder.strip('/') or 'Resources'}/{safe_title}.md"
    if vault.exists(rel):
        rel = f"{folder.strip('/') or 'Resources'}/{safe_title} {datetime.now().strftime('%Y-%m-%d %H%M%S')}.md"
    content = (f"---\nsource: {final_url}\ncaptured: {now()[:10]}\ntags: [web-clip]\n---\n\n"
               f"# {title}\n\nSource: <{final_url}>\n\n{text}\n")
    res = vault.create(rel, content, actor, chat_id=chat_id)
    if chat_id:
        with db.connect() as conn:
            conn.execute("INSERT INTO attachments(id,chat_id,filename,mime_type,path,extracted_text,created_at,size,note_path)"
                         " VALUES(?,?,?,?,?,?,?,?,?)",
                         (uuid.uuid4().hex, chat_id, title, "text/html", "", text, now(), len(text), res["path"]))
    return {"path": res["path"], "title": title, "url": final_url, "chars": len(text)}


__all__ = ["save_upload", "get", "delete", "save_to_vault", "ingest_link", "fetch_url", "extract_text", "slugify"]
