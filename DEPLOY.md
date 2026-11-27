# Deploying

## Docker (recommended)

```bash
git clone https://github.com/hamzaibnzakir/Knowledgeos.git /opt/knowledge-os
cd /opt/knowledge-os
cp .env.example .env && nano .env
docker compose up -d --build
docker compose exec knowledge-os python -m knowledge_os set-password
docker compose logs -f
```

Both ports are published on `127.0.0.1` only. Choose how you reach them:

- **SSH tunnel (simplest, private):** `ssh -L 8786:localhost:8786 root@your-vps`, then open http://localhost:8786.
- **HTTPS reverse proxy (recommended for phone access and MCP):** e.g. Caddy:
  ```
  kos.example.com          { reverse_proxy 127.0.0.1:8786 }
  mcp-kos.example.com      { reverse_proxy 127.0.0.1:8796 }
  ```
  Nginx works the same way (`proxy_pass http://127.0.0.1:8796;` plus `proxy_set_header Host $host; proxy_set_header X-Forwarded-Proto $scheme; proxy_buffering off;` for MCP streaming). Set `KOS_MCP_PUBLIC_BASE=https://mcp-kos.example.com`.
- **Direct IP:port:** set `KOS_BIND=0.0.0.0`. Traffic, including your password, is then unencrypted; only do this on a trusted network or behind a firewall allow-list.

Update: `git pull && docker compose up -d --build`. Data lives in the mounted folder and survives rebuilds and reboots (`restart: unless-stopped`).

## Upgrading the v1 install on the VPS

v1 ran from `/root/personal-ai-knowledge-os` with two systemd services on ports 8786 (web) and 8796 (MCP, behind `mcp-knowledgeos.brainboxecomlab.com`). v2 reuses the same data folder and ports.

1. **Back up v1 data**
   ```bash
   cp -a /root/personal-ai-knowledge-os/data /root/kos-v1-data-$(date +%F)
   ```
2. **Stop the old services** (check the exact names first)
   ```bash
   systemctl list-units --all | grep -i knowledge
   systemctl disable --now knowledge-os-web knowledge-os-mcp
   ```
3. **Install v2** next to it
   ```bash
   git clone https://github.com/hamzaibnzakir/Knowledgeos.git /opt/knowledge-os && cd /opt/knowledge-os
   cp .env.example .env
   ```
   In `.env` set:
   ```
   KOS_DATA_HOST_DIR=/root/personal-ai-knowledge-os/data
   KOS_MCP_PUBLIC_BASE=https://mcp-knowledgeos.brainboxecomlab.com
   MCP_AUTH_TOKEN=<the same token Hermes uses today>
   ```
   Your v1 `KNOWLEDGE_OS_GATE_HASH` / `KNOWLEDGE_OS_GATE_SALT` also still work until you set a new password.
4. **Give the container user access to the data** (it runs as uid 1000, not root)
   ```bash
   chown -R 1000:1000 /root/personal-ai-knowledge-os/data
   ```
5. **Start and set a real password** (a passphrase is much stronger than the old 6-digit code)
   ```bash
   docker compose up -d --build
   docker compose exec knowledge-os python -m knowledge_os set-password
   docker compose logs --tail 50
   ```
   On first start the database upgrades in place: notes are re-indexed, database memories are written into `Memory/` files, old chats are exported to `Archive/Chats/`, and the audit log and agent stats are kept.
6. **Proxy:** the existing Nginx site for `mcp-knowledgeos.brainboxecomlab.com` → `127.0.0.1:8796` keeps working. If you used to open the web app at `http://<VPS IP>:8786`, either tunnel over SSH or add an HTTPS site for 8786 (see above).
7. **Agents:** Hermes keeps working with `MCP_AUTH_TOKEN` and the old tool names. OAuth clients (Claude, ChatGPT) must reconnect once because v1 OAuth tokens are not carried over. Afterwards, issue Hermes its own token in *Agents & MCP* and remove `MCP_AUTH_TOKEN`.
8. **Optional tidy-up:** v1 wrote a log file into `Agents/<agent>/Logs/` on every MCP call. v2 records calls in the activity log instead and no longer writes those files. Delete or archive the old ones if you don't want them in search.

Rollback: `docker compose down`, restore the backup folder, `systemctl enable --now` the old services.

## Upgrading v2 → v3 (memory restructure, no built-in AI)

`git pull && docker compose up -d --build`. On start, v3:

- writes your existing memories into `Memory/` (`profile`/`goal` → `Profile.md`, `preference`/`communication` → `Rules.md`, others → `Memory/Topics/`), keeping their old ids as `m<id>`;
- exports old chats to `Archive/Chats/` as Markdown;
- adds `Skills/Memory tidy-up.md`.

`KOS_AI_*` / `OPENAI_API_KEY` are no longer used and can be removed from `.env`. Set `KOS_MCP_WRITE_TOKEN_NAME=Hermes` (the default) so facts Hermes saves through `MCP_AUTH_TOKEN` are attributed to Hermes.

On the **Agents** page you can set each agent to *Read only*, *Reviewed* or *Trusted* (its facts skip Review) and pause it with one switch. The page also shows whether each agent calls `brief` at the start of its sessions.

On a phone, open the site and use *Add to Home Screen*: it runs full screen with its own icon, a bottom tab bar, a quick-capture button and swipe-to-review.

Tell Hermes: call `brief` at the start of each session, save facts with `remember` (subject + source), and log work with `log`. Old tool names (`save_memory`, `search_memory`, `forget_memory`, …) keep working.

## Without Docker (systemd + venv)

```bash
apt install -y python3-venv poppler-utils tesseract-ocr nodejs npm
git clone https://github.com/hamzaibnzakir/Knowledgeos.git /opt/knowledge-os && cd /opt/knowledge-os
(cd frontend && npm ci && npm run build)
python3 -m venv .venv && .venv/bin/pip install "./backend[local-embeddings]"
cp .env.example .env   # set KOS_DATA_DIR=/opt/knowledge-os/data (or your v1 data folder)
.venv/bin/python -m knowledge_os set-password
```

`/etc/systemd/system/knowledge-os.service`:

```ini
[Unit]
Description=Knowledge OS
After=network-online.target

[Service]
WorkingDirectory=/opt/knowledge-os
EnvironmentFile=/opt/knowledge-os/.env
Environment=KOS_FRONTEND_DIST=/opt/knowledge-os/frontend/dist
ExecStart=/opt/knowledge-os/.venv/bin/python -m knowledge_os serve
Restart=on-failure
RestartSec=3
User=kos
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=/opt/knowledge-os/data

[Install]
WantedBy=multi-user.target
```

`systemctl daemon-reload && systemctl enable --now knowledge-os`.

## Operations

| Task | Command (Docker: prefix with `docker compose exec knowledge-os`) |
|---|---|
| Set / reset password | `python -m knowledge_os set-password` |
| Health and config | `python -m knowledge_os doctor` |
| Re-scan the vault | `python -m knowledge_os reindex` (`--full` rebuilds) |
| Embed everything now | `python -m knowledge_os embed` |
| Backup now | `python -m knowledge_os backup` |
| Token for an agent | `python -m knowledge_os create-token Hermes --write` |

Backups run automatically (`KOS_BACKUP_INTERVAL_HOURS`, default 24; keeps 14). Restore by stopping the service and extracting a backup into the data folder: `vault/` and `.brainbox/knowledge.db`.
