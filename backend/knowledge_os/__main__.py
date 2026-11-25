"""Command line: `python -m knowledge_os <command>`.

  serve                 run web app + MCP server + background workers
  set-password          set the web/OAuth password (prompts; stored hashed)
  reindex [--full]      reconcile or fully rebuild the index from the vault
  embed                 embed all pending chunks now
  backup                create a backup now
  create-token NAME [--write]   issue an agent token for an MCP client
  doctor                print configuration and health
"""
from __future__ import annotations

import argparse
import getpass
import json
import sys


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="knowledge_os", description="Personal AI Knowledge OS")
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("serve")
    sp = sub.add_parser("set-password")
    sp.add_argument("--stdin", action="store_true", help="read the password from stdin (for scripts)")
    r = sub.add_parser("reindex")
    r.add_argument("--full", action="store_true")
    sub.add_parser("embed")
    sub.add_parser("backup")
    t = sub.add_parser("create-token")
    t.add_argument("name")
    t.add_argument("--write", action="store_true", help="grant knowledge:write as well as read")
    sub.add_parser("doctor")
    args = p.parse_args(argv)

    if args.cmd in (None, "serve"):
        from .service import serve
        serve()
        return 0

    from . import db
    db.ensure_schema()

    if args.cmd == "set-password":
        from . import auth
        if args.stdin:
            pw = sys.stdin.readline().rstrip("\n")
        else:
            pw = getpass.getpass("New password: ")
            if pw != getpass.getpass("Repeat: "):
                print("Passwords do not match", file=sys.stderr)
                return 1
        auth.set_password(pw)
        print("Password saved. Existing sessions were signed out.")
    elif args.cmd == "reindex":
        from . import indexer
        print(json.dumps(indexer.rebuild() if args.full else indexer.reconcile()))
    elif args.cmd == "embed":
        from . import embeddings
        total = 0
        while n := embeddings.embed_pending():
            total += n
            print(f"embedded {total}…", flush=True)
        print(json.dumps(embeddings.status()))
    elif args.cmd == "backup":
        from . import backups
        print(json.dumps(backups.create_backup(actor="cli")))
    elif args.cmd == "create-token":
        from . import oauth
        scopes = ["knowledge:read", "knowledge:write"] if args.write else ["knowledge:read"]
        res = oauth.create_agent_token(args.name, scopes)
        print(f"Token for {res['label']} ({' '.join(res['scopes'])}) — shown once:\n{res['token']}")
    elif args.cmd == "doctor":
        from . import ai, auth, embeddings, indexer
        from .config import get_settings
        s = get_settings()
        report = {
            "data_dir": str(s.data_dir), "vault": str(s.vault_dir), "db": str(s.db_path),
            "password_configured": auth.password_configured(),
            "ai": ai.get_gateway().describe(), "embeddings": embeddings.status(),
            "stats": indexer.stats(), "mcp_public_base": s.mcp_public_base,
        }
        print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
