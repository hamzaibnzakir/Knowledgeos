"""Process entry point: one process serves the web app and the MCP server on two
ports and runs the background workers (index reconcile, embeddings, backups).

One process means one embedding model in memory, one write lock for the vault
and no cross-process races on SQLite or state files.
"""
from __future__ import annotations

import asyncio
import logging
import threading

import uvicorn

from . import backups, db, embeddings, indexer
from .config import get_settings

log = logging.getLogger("kos")


class Workers:
    def __init__(self):
        self.stop = threading.Event()
        self.threads: list[threading.Thread] = []

    def start(self):
        s = get_settings()
        self.threads = [
            threading.Thread(target=self._reconcile_loop, name="kos-reconcile", daemon=True),
            threading.Thread(target=embeddings.worker_loop, args=(self.stop,), name="kos-embed", daemon=True),
            threading.Thread(target=self._backup_loop, name="kos-backup", daemon=True),
        ]
        for t in self.threads:
            t.start()
        emb = embeddings.get_embedder()
        if emb.available and hasattr(emb, "warm_async"):
            emb.warm_async()
        embeddings.notify()
        log.info("Background workers started (reconcile every %ss, backups every %sh)",
                 s.reconcile_interval, s.backup_interval_hours)

    def shutdown(self):
        self.stop.set()
        embeddings.notify()

    def _reconcile_loop(self):
        interval = max(5, get_settings().reconcile_interval)
        while not self.stop.wait(interval):
            try:
                stats = indexer.reconcile()
                if stats["indexed"] or stats["removed"]:
                    log.info("Picked up external vault changes: %s", stats)
            except Exception:
                log.exception("reconcile failed")

    def _backup_loop(self):
        hours = get_settings().backup_interval_hours
        if hours <= 0:
            return
        while not self.stop.wait(300):
            try:
                if backups.due(hours):
                    log.info("Scheduled backup: %s", backups.create_backup())
            except Exception:
                log.exception("scheduled backup failed")


def startup() -> None:
    s = get_settings()
    db.ensure_schema()
    stats = indexer.reconcile()
    log.info("Vault %s indexed: %s", s.vault_dir, stats)


def serve() -> None:
    from .mcp_app import build_mcp_app
    from .web import create_app

    s = get_settings()
    logging.basicConfig(level=s.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    startup()
    workers = Workers()
    if s.background_workers:
        workers.start()

    servers = [uvicorn.Server(uvicorn.Config(create_app(), host=s.web_host, port=s.web_port, proxy_headers=True,
                                             forwarded_allow_ips="*", log_level=s.log_level.lower(),
                                             server_header=False))]
    if s.mcp_enabled:
        servers.append(uvicorn.Server(uvicorn.Config(build_mcp_app(), host=s.mcp_host, port=s.mcp_port,
                                                     proxy_headers=True, forwarded_allow_ips="*",
                                                     log_level=s.log_level.lower(), server_header=False)))
    log.info("Web app on http://%s:%s%s", s.web_host, s.web_port,
             f" · MCP on http://{s.mcp_host}:{s.mcp_port}/mcp (public: {s.mcp_public_base}/mcp)" if s.mcp_enabled else "")

    async def main():
        await asyncio.gather(*(srv.serve() for srv in servers))

    try:
        asyncio.run(main())
    finally:
        workers.shutdown()
