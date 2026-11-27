"""HTTP API for the web app. The browser only ever talks to this API; the API is
the only thing that mutates the vault (via the vault/memory services)."""
from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field
from starlette.middleware.base import BaseHTTPMiddleware

from . import (attachments, auth, backups, db, embeddings, indexer, mcp_app, memory, oauth, retrieval,
               tools, vault)
from .config import get_settings
from .util import Forbidden, KOSError, jloads, now, record_event

log = logging.getLogger("kos.web")
VERSION = "3.0.0"

PUBLIC_API = {"/api/auth/status", "/api/auth/login", "/api/health"}
CSRF_HEADER = "x-kos"


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


class SecurityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path.startswith("/api/"):
            if path not in PUBLIC_API:
                if not auth.validate_session(request.cookies.get(auth.SESSION_COOKIE)):
                    return JSONResponse({"detail": "Sign in required"}, status_code=401)
            if request.method not in ("GET", "HEAD", "OPTIONS") and request.headers.get(CSRF_HEADER) != "1":
                return JSONResponse({"detail": "Missing X-KOS header"}, status_code=403)
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault("X-Frame-Options", "DENY")
        if not path.startswith("/api/"):
            response.headers.setdefault(
                "Content-Security-Policy",
                "default-src 'self'; img-src 'self' data: blob: https:; style-src 'self' 'unsafe-inline' "
                "https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com data:; "
                "script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'")
        if path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response


def create_app() -> FastAPI:
    app = FastAPI(title="Knowledge OS", version=VERSION, docs_url=None, redoc_url=None, openapi_url="/api/openapi.json")
    app.add_middleware(SecurityMiddleware)

    @app.exception_handler(KOSError)
    async def kos_error(request: Request, exc: KOSError):
        body: dict[str, Any] = {"detail": exc.message}
        body.update({k: v for k, v in exc.extra.items() if k in ("current_hash", "path")})
        return JSONResponse(body, status_code=exc.status)

    _routes(app)
    _static(app)
    return app


def _secure_cookie(request: Request) -> bool:
    mode = get_settings().cookie_secure.lower()
    if mode in ("true", "1", "yes"):
        return True
    if mode in ("false", "0", "no"):
        return False
    return request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"


# --------------------------------------------------------------------------- request models

class LoginIn(BaseModel):
    password: str = Field(..., max_length=500)


class PasswordIn(BaseModel):
    current: str
    new: str = Field(..., min_length=6, max_length=500)


class NoteCreate(BaseModel):
    path: str
    content: str = ""


class NoteUpdate(BaseModel):
    path: str
    content: str
    expected_hash: str | None = None


class NoteMove(BaseModel):
    path: str
    new_path: str
    update_links: bool = True


class FavoriteIn(BaseModel):
    path: str
    favorite: bool = True


class FolderIn(BaseModel):
    path: str


class CaptureIn(BaseModel):
    text: str = Field(..., min_length=1, max_length=100_000)
    title: str | None = None


class FactIn(BaseModel):
    subject: str = Field(..., min_length=1, max_length=120)
    fact: str = Field(..., min_length=1, max_length=600)
    kind: str = "topic"
    source: str = ""
    replaces: str | None = None


class FactEdit(BaseModel):
    fact: str = Field(..., min_length=1, max_length=600)
    in_place: bool = False   # fix a typo without keeping the old wording in History


class ForgetIn(BaseModel):
    reason: str = ""


class ProposalDecision(BaseModel):
    approve: bool
    text: str | None = None


class OwnerNameIn(BaseModel):
    name: str = Field("", max_length=80)


class SubjectIn(BaseModel):
    subject: str = Field(..., min_length=1, max_length=120)
    kind: str = "topic"


class LinkIn(BaseModel):
    url: str = Field(..., max_length=2000)
    folder: str = "Resources"


class SkillIn(BaseModel):
    title: str
    content: str
    service: str = ""


class TokenIn(BaseModel):
    label: str
    scopes: list[str] = ["knowledge:read"]


class LabelIn(BaseModel):
    label: str


class ReindexIn(BaseModel):
    full: bool = False


# --------------------------------------------------------------------------- routes

def _routes(app: FastAPI) -> None:  # noqa: C901 - flat route table is easier to scan
    WEB = "web"

    # ---- auth
    @app.get("/api/health")
    def health():
        return {"status": "ok", "version": VERSION}

    @app.get("/api/auth/status")
    def auth_status(request: Request):
        return {"authenticated": auth.validate_session(request.cookies.get(auth.SESSION_COOKIE)),
                "password_configured": auth.password_configured()}

    @app.post("/api/auth/login")
    def login(x: LoginIn, request: Request):
        auth.verify_password_rate_limited(x.password, client_ip(request))
        token, max_age = auth.create_session(client_ip(request), request.headers.get("user-agent", ""))
        resp = JSONResponse({"ok": True})
        resp.set_cookie(auth.SESSION_COOKIE, token, max_age=max_age, httponly=True, secure=_secure_cookie(request),
                        samesite="strict", path="/")
        return resp

    @app.post("/api/auth/logout")
    def logout(request: Request):
        auth.revoke_session(request.cookies.get(auth.SESSION_COOKIE))
        resp = JSONResponse({"ok": True})
        resp.delete_cookie(auth.SESSION_COOKIE, path="/")
        return resp

    @app.get("/api/auth/sessions")
    def sessions(request: Request):
        return auth.list_sessions(request.cookies.get(auth.SESSION_COOKIE))

    @app.post("/api/auth/sessions/revoke-all")
    def revoke_all():
        auth.revoke_all_sessions()
        resp = JSONResponse({"ok": True})
        resp.delete_cookie(auth.SESSION_COOKIE, path="/")
        return resp

    @app.post("/api/auth/password")
    def change_password(x: PasswordIn, request: Request):
        auth.verify_password_rate_limited(x.current, client_ip(request), purpose="password_change")
        auth.set_password(x.new)
        resp = JSONResponse({"ok": True, "message": "Password changed. All sessions were signed out."})
        resp.delete_cookie(auth.SESSION_COOKIE, path="/")
        return resp

    # ---- notes
    @app.get("/api/notes")
    def list_notes(folder: str | None = None, tag: str | None = None, sort: str = "updated",
                   limit: int = Query(500, le=5000), offset: int = 0, filter: str | None = None):
        if filter == "orphans":
            return indexer.orphans()
        if filter == "favorites":
            with db.connect() as conn:
                favs = [r[0] for r in conn.execute("SELECT path FROM favorites ORDER BY created_at DESC")]
            notes = {n["path"]: n for n in tools.query_notes(limit=5000)}
            return [notes[p] for p in favs if p in notes]
        return tools.query_notes(folder=folder, tag=tag, sort=sort, limit=limit, offset=offset)

    @app.get("/api/notes/read")
    def read_note(path: str):
        n = vault.read(path)
        with db.connect() as conn:
            fav = bool(conn.execute("SELECT 1 FROM favorites WHERE path=?", (n["path"],)).fetchone())
            meta = conn.execute("SELECT title,tags,aliases,properties,created_at,updated_at,word_count FROM notes "
                                "WHERE path=?", (n["path"],)).fetchone()
        out = {**n, "favorite": fav}
        if meta:
            out.update({"title": meta["title"], "tags": jloads(meta["tags"], []), "aliases": jloads(meta["aliases"], []),
                        "properties": jloads(meta["properties"], {}), "created_at": meta["created_at"],
                        "updated_at": meta["updated_at"], "word_count": meta["word_count"]})
        return out

    @app.get("/api/notes/context")
    def note_context(path: str):
        clean = vault.normalize_rel(path)
        return {"backlinks": [{"path": p, "title": _title(p)} for p in indexer.backlinks(clean)],
                "outgoing": indexer.outgoing(clean),
                "related": retrieval.related_notes(clean),
                "unlinked_mentions": indexer.unlinked_mentions(clean),
                "versions": len(vault.list_versions(clean))}

    @app.post("/api/notes")
    def create_note(x: NoteCreate):
        return vault.create(x.path, x.content, WEB)

    @app.put("/api/notes")
    def update_note(x: NoteUpdate):
        return vault.write(x.path, x.content, WEB, expected_hash=x.expected_hash)

    @app.post("/api/notes/move")
    def move_note(x: NoteMove):
        return vault.move(x.path, x.new_path, WEB, update_links=x.update_links)

    @app.delete("/api/notes")
    def delete_note(path: str):
        return vault.delete(path, WEB)

    @app.post("/api/notes/favorite")
    def favorite(x: FavoriteIn):
        clean = vault.normalize_rel(x.path)
        with db.connect() as conn:
            if x.favorite:
                conn.execute("INSERT OR IGNORE INTO favorites(path,created_at) VALUES(?,?)", (clean, now()))
            else:
                conn.execute("DELETE FROM favorites WHERE path=?", (clean,))
        return {"ok": True, "favorite": x.favorite}

    @app.get("/api/notes/versions")
    def versions(path: str):
        return vault.list_versions(path)

    @app.get("/api/versions/{version_id}")
    def version(version_id: int):
        return vault.get_version(version_id)

    @app.post("/api/versions/{version_id}/restore")
    def restore_version(version_id: int):
        return vault.restore_version(version_id, WEB)

    @app.post("/api/notes/daily")
    def daily_note():
        today = date.today().isoformat()
        rel = f"Notes/Daily/{today}.md"
        if not vault.exists(rel):
            vault.create(rel, f"---\ntags: [daily]\ncreated: {today}\n---\n\n# {today}\n\n", WEB)
        return {"path": rel}

    @app.post("/api/capture")
    def capture(x: CaptureIn):
        stamp = now()
        title = (x.title or x.text.strip().splitlines()[0][:60] or "Capture").strip()
        safe_title = "".join(c if c.isalnum() or c in " -_" else " " for c in title).strip() or "Capture"
        rel = f"Inbox/{stamp[:10]} {safe_title}.md"
        i = 2
        while vault.exists(rel):
            rel = f"Inbox/{stamp[:10]} {safe_title} {i}.md"
            i += 1
        return vault.create(rel, f"---\ncaptured: {stamp}\ntags: [inbox]\n---\n\n{x.text.strip()}\n", WEB)

    # ---- folders, tags, files
    @app.get("/api/folders")
    def folders():
        return vault.folder_tree()

    @app.post("/api/folders")
    def create_folder(x: FolderIn):
        return vault.create_folder(x.path, WEB)

    @app.get("/api/tags")
    def tags():
        return indexer.tags()

    @app.get("/api/files")
    def get_file(path: str):
        p = vault.resolve_asset(path)
        return FileResponse(p, headers={"Content-Security-Policy": "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'",
                                        "X-Content-Type-Options": "nosniff"},
                            content_disposition_type="inline" if p.suffix.lower() in
                            (".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf") else "attachment")

    @app.post("/api/files")
    async def upload_file(file: UploadFile = File(...), folder: str = Form("Attachments")):
        data = await file.read(get_settings().max_upload_mb * 1024 * 1024 + 1)
        if len(data) > get_settings().max_upload_mb * 1024 * 1024:
            raise KOSError("File too large", status=413)
        name = Path(file.filename or "file").name
        rel = vault.save_asset(f"{vault.normalize_folder(folder) or 'Attachments'}/{name}", data, WEB)
        return {"path": rel}

    # ---- search & graph
    @app.get("/api/search")
    def search(q: str, mode: str = "hybrid", folder: str | None = None, tag: str | None = None,
               limit: int = Query(30, le=100)):
        if mode not in ("hybrid", "keyword", "semantic"):
            mode = "hybrid"
        return retrieval.search(q, limit=limit, mode=mode, folder=folder, tag=tag)

    @app.get("/api/quick")
    def quick(q: str = "", limit: int = Query(12, le=50)):
        return retrieval.quick_find(q, limit)

    @app.get("/api/graph")
    def graph(tags: bool = False, unresolved: bool = False, folder: str | None = None):
        return indexer.graph(include_tags=tags, include_unresolved=unresolved, folder=folder)

    @app.get("/api/graph/local")
    def local_graph(path: str, depth: int = Query(1, ge=1, le=3), tags: bool = False):
        return indexer.local_graph(vault.normalize_rel(path), depth=depth, include_tags=tags)

    @app.get("/api/graph/orphans")
    def orphans():
        return indexer.orphans()

    @app.get("/api/graph/unresolved")
    def unresolved():
        return indexer.unresolved_links()

    # ---- memory
    OWNER = dict(agent="you", is_owner=True, actor=WEB)

    @app.get("/api/memory/subjects")
    def mem_subjects():
        return memory.subjects()

    @app.post("/api/memory/subjects")
    def mem_new_subject(x: SubjectIn):
        path = memory.resolve_subject(x.subject, x.kind)
        if vault.exists(path):
            raise KOSError("That subject already exists", status=409, path=path)
        memory._ensure_file(path, WEB)
        return {"path": path}

    @app.get("/api/memory/facts")
    def mem_facts(path: str | None = None, review: str | None = None, status: str = "current", q: str | None = None,
                  by: str | None = None, limit: int = Query(500, le=5000)):
        return memory.list_facts(path=path, review=review, status=status, q=q, by=by, limit=limit)

    @app.get("/api/memory/search")
    def mem_search(q: str, limit: int = Query(20, le=100)):
        return memory.search_facts(q, k=limit)

    @app.post("/api/memory/facts")
    def mem_add(x: FactIn):
        return memory.remember(x.subject, x.fact, kind=x.kind, src=x.source, replaces=x.replaces,
                               on_similar="ask", **OWNER)

    @app.post("/api/memory/facts/force")
    def mem_add_force(x: FactIn):
        return memory.remember(x.subject, x.fact, kind=x.kind, src=x.source, replaces=x.replaces,
                               on_similar="add", **OWNER)

    @app.patch("/api/memory/facts/{fid}")
    def mem_edit(fid: str, x: FactEdit):
        if x.in_place:
            return memory.edit_fact_text(fid, x.fact, actor=WEB)
        f = memory._get_fact(fid)
        return memory.remember(f["path"], x.fact, replaces=fid, on_similar="add", **OWNER)

    @app.post("/api/memory/facts/{fid}/forget")
    def mem_forget(fid: str, x: ForgetIn):
        return memory.forget(fid, reason=x.reason, **OWNER)

    @app.delete("/api/memory/facts/{fid}")
    def mem_purge(fid: str):
        return memory.purge_fact(fid, actor=WEB)

    @app.get("/api/memory/review")
    def mem_review():
        return memory.review_queue()

    @app.post("/api/memory/facts/{fid}/approve")
    def mem_approve(fid: str):
        return memory.approve_fact(fid, actor=WEB)

    @app.post("/api/memory/facts/{fid}/reject")
    def mem_reject(fid: str):
        return memory.reject_fact(fid, actor=WEB)

    @app.post("/api/memory/proposals/{pid}")
    def mem_proposal(pid: str, x: ProposalDecision):
        return memory.resolve_proposal(pid, x.approve, text=x.text, actor=WEB)

    @app.get("/api/memory/health")
    def mem_health():
        return memory.health()

    @app.get("/api/memory/brief")
    def mem_brief(agent: str = "Hermes", topic: str | None = None):
        return memory.brief(agent, topic)

    # ---- import (files and links become notes)
    @app.post("/api/import/file")
    async def import_file(file: UploadFile = File(...), folder: str = Form("Inbox")):
        staged = await attachments.save_upload(file, None, actor=WEB)
        name = staged["filename"].rsplit(".", 1)[0]
        out = attachments.save_to_vault(staged["id"], f"{vault.normalize_folder(folder) or 'Inbox'}/{name}", WEB)
        return {**out, "extraction": staged["extraction"], "chars": staged["extracted_chars"]}

    @app.post("/api/import/link")
    def import_link(x: LinkIn):
        return attachments.ingest_link(x.url, folder=x.folder, actor=WEB)

    # ---- skills
    @app.get("/api/skills")
    def skills():
        return tools.call("list_skills", {"limit": 500}, tools.ToolContext(actor=WEB))

    @app.post("/api/skills")
    def create_skill(x: SkillIn):
        return tools.call("create_skill", x.model_dump(), tools.ToolContext(actor=WEB))

    # ---- agents & MCP
    @app.get("/api/mcp")
    def mcp_info():
        return mcp_app.describe()

    @app.get("/api/agents")
    def agents_list():
        return oauth.list_credentials()

    @app.post("/api/agents/tokens")
    def create_token(x: TokenIn):
        return oauth.create_agent_token(x.label, x.scopes)

    @app.post("/api/agents/{client_id:path}/revoke")
    def revoke(client_id: str):
        return oauth.revoke_client(client_id)

    @app.patch("/api/agents/{client_id:path}")
    def relabel(client_id: str, x: LabelIn):
        return oauth.rename_client(client_id, x.label)

    @app.get("/api/tools")
    def tool_list():
        return tools.tool_catalog()

    # ---- activity, usage, system
    @app.get("/api/audit")
    def audit(limit: int = Query(100, le=500), actor: str | None = None, chat_id: str | None = None,
              before: int | None = None):
        return tools.audit_events(limit=limit, actor=actor, chat_id=chat_id, before_id=before)

    @app.get("/api/system")
    def system():
        s = get_settings()
        with db.connect() as conn:
            last_backup = db.get_meta(conn, "last_backup")
            owner_name = db.get_meta(conn, "owner_name") or ""
        return {"version": VERSION, "stats": indexer.stats(), "owner_name": owner_name,
                "embeddings": embeddings.status(), "last_backup": last_backup,
                "mcp_enabled": s.mcp_enabled, "context_token_budget": s.context_token_budget,
                "memory": {"brief_token_budget": s.brief_token_budget, "core_max_chars": s.core_memory_max_chars,
                           "stale_days": s.memory_stale_days, "write_token_name": s.mcp_write_token_name},
                "limits": {"max_upload_mb": s.max_upload_mb, "versions_keep": s.versions_keep,
                           "backup_keep": s.backup_keep, "backup_interval_hours": s.backup_interval_hours}}

    @app.put("/api/owner-name")
    def set_owner_name(x: OwnerNameIn):
        name = " ".join(x.name.split())[:40]
        with db.connect() as conn, db.transaction(conn):
            db.set_meta(conn, "owner_name", name)
        return {"owner_name": name}

    @app.post("/api/reindex")
    def reindex(x: ReindexIn):
        stats = indexer.rebuild() if x.full else indexer.reconcile()
        with db.connect() as conn:
            record_event(conn, "index.rebuild" if x.full else "index.reconcile", details=stats, actor=WEB)
        return stats

    @app.post("/api/embeddings/run")
    def run_embeddings():
        if not embeddings.get_embedder().available:
            raise KOSError("Embeddings are disabled or unavailable", status=409)
        embeddings.notify()
        return embeddings.status()

    @app.get("/api/trash")
    def trash():
        return vault.list_trash()

    @app.post("/api/trash/{trash_id}/restore")
    def restore_trash(trash_id: str):
        return vault.restore_trash(trash_id, WEB)

    @app.delete("/api/trash/{trash_id}")
    def purge_trash(trash_id: str):
        return vault.purge_trash(trash_id, WEB)

    @app.get("/api/backups")
    def list_backups():
        return backups.list_backups()

    @app.post("/api/backups")
    def make_backup():
        return backups.create_backup(actor=WEB)

    @app.get("/api/backups/{name}")
    def download_backup(name: str):
        return FileResponse(backups.backup_path(name), filename=name, media_type="application/gzip")

    @app.get("/api/export")
    def export_vault():
        data = backups.export_vault_zip()
        return Response(data, media_type="application/zip",
                        headers={"Content-Disposition": f'attachment; filename="vault-{date.today().isoformat()}.zip"'})


def _title(path: str) -> str:
    with db.connect() as conn:
        r = conn.execute("SELECT title FROM notes WHERE path=?", (path,)).fetchone()
    return r["title"] if r else path


def _static(app: FastAPI) -> None:
    s = get_settings()
    candidates = [s.frontend_dist] if s.frontend_dist else []
    candidates += [Path(__file__).resolve().parent / "static", Path(__file__).resolve().parents[2] / "frontend" / "dist"]
    dist = next((c for c in candidates if c and (c / "index.html").is_file()), None)
    if not dist:
        @app.get("/")
        def no_frontend():
            return JSONResponse({"detail": "Frontend not built. Run `npm run build` in frontend/ or use the Docker image."})
        return
    dist = dist.resolve()
    index = dist / "index.html"

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str):
        if full_path.startswith("api/"):
            return JSONResponse({"detail": "Not found"}, status_code=404)
        target = (dist / full_path).resolve()
        if full_path and target.is_file() and target.is_relative_to(dist):
            cache = "public, max-age=31536000, immutable" if full_path.startswith("assets/") else "no-cache"
            return FileResponse(target, headers={"Cache-Control": cache})
        return FileResponse(index, headers={"Cache-Control": "no-cache"})


__all__ = ["create_app", "Forbidden"]
