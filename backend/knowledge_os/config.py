"""Runtime configuration.

Everything comes from environment variables (or a .env file). Legacy variable
names from the first version (OPENAI_API_KEY, MCP_AUTH_TOKEN,
KNOWLEDGE_OS_GATE_HASH, ...) are still honoured so an existing deployment keeps
working after the upgrade.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, AliasChoices
from pydantic_settings import BaseSettings, SettingsConfigDict


def _env(*names: str) -> AliasChoices:
    return AliasChoices(*names)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)

    # --- storage -------------------------------------------------------------
    root: Path = Field(Path("."), validation_alias=_env("KOS_ROOT", "KNOWLEDGE_OS_ROOT"))
    data_dir_override: Path | None = Field(None, validation_alias=_env("KOS_DATA_DIR"))
    models_dir_override: Path | None = Field(None, validation_alias=_env("KOS_MODELS_DIR"))

    # --- network -------------------------------------------------------------
    web_host: str = Field("127.0.0.1", validation_alias=_env("KOS_WEB_HOST"))
    web_port: int = Field(8786, validation_alias=_env("KOS_WEB_PORT"))
    mcp_host: str = Field("127.0.0.1", validation_alias=_env("KOS_MCP_HOST"))
    mcp_port: int = Field(8796, validation_alias=_env("KOS_MCP_PORT"))
    mcp_enabled: bool = Field(True, validation_alias=_env("KOS_MCP_ENABLED"))
    mcp_public_base: str = Field("http://localhost:8796", validation_alias=_env("KOS_MCP_PUBLIC_BASE", "MCP_PUBLIC_BASE"))
    mcp_allowed_hosts: str = Field("", validation_alias=_env("KOS_MCP_ALLOWED_HOSTS"))
    # Static bearer tokens kept for existing agents (e.g. Hermes). Prefer UI-issued agent tokens.
    mcp_auth_token: str = Field("", validation_alias=_env("KOS_MCP_WRITE_TOKEN", "MCP_AUTH_TOKEN"))
    mcp_read_token: str = Field("", validation_alias=_env("KOS_MCP_READ_TOKEN", "MCP_READ_TOKEN"))
    mcp_write_rate_per_min: int = Field(120, validation_alias=_env("KOS_MCP_WRITE_RATE_PER_MIN"))
    mcp_read_rate_per_min: int = Field(600, validation_alias=_env("KOS_MCP_READ_RATE_PER_MIN"))
    oauth_access_ttl: int = Field(3600, validation_alias=_env("KOS_OAUTH_ACCESS_TTL"))
    oauth_refresh_ttl: int = Field(60 * 60 * 24 * 30, validation_alias=_env("KOS_OAUTH_REFRESH_TTL"))

    # --- web auth ------------------------------------------------------------
    gate_hash: str = Field("", validation_alias=_env("KOS_PASSWORD_HASH", "KNOWLEDGE_OS_GATE_HASH"))
    gate_salt: str = Field("", validation_alias=_env("KOS_PASSWORD_SALT", "KNOWLEDGE_OS_GATE_SALT"))
    session_days: int = Field(7, validation_alias=_env("KOS_SESSION_DAYS"))
    cookie_secure: str = Field("auto", validation_alias=_env("KOS_COOKIE_SECURE"))  # auto|true|false
    login_max_failures: int = Field(5, validation_alias=_env("KOS_LOGIN_MAX_FAILURES"))
    login_lock_minutes: int = Field(15, validation_alias=_env("KOS_LOGIN_LOCK_MINUTES"))
    login_global_max_failures: int = Field(30, validation_alias=_env("KOS_LOGIN_GLOBAL_MAX_FAILURES"))

    # --- AI gateway -------------------------------------------------------------
    ai_provider: str = Field("openai", validation_alias=_env("KOS_AI_PROVIDER"))
    ai_base_url: str = Field("", validation_alias=_env("KOS_AI_BASE_URL"))
    ai_api_key: str = Field("", validation_alias=_env("KOS_AI_API_KEY", "OPENAI_API_KEY"))
    chat_model: str = Field("gpt-4o-mini", validation_alias=_env("KOS_CHAT_MODEL", "OPENAI_MODEL"))
    agent_model: str = Field("", validation_alias=_env("KOS_AGENT_MODEL"))
    utility_model: str = Field("", validation_alias=_env("KOS_UTILITY_MODEL"))
    ai_temperature: float = Field(0.2, validation_alias=_env("KOS_AI_TEMPERATURE"))
    ai_timeout: float = Field(90.0, validation_alias=_env("KOS_AI_TIMEOUT"))
    agent_max_steps: int = Field(6, validation_alias=_env("KOS_AGENT_MAX_STEPS"))
    context_token_budget: int = Field(5000, validation_alias=_env("KOS_CONTEXT_TOKEN_BUDGET"))
    history_messages: int = Field(10, validation_alias=_env("KOS_HISTORY_MESSAGES"))
    pricing_json: str = Field("", validation_alias=_env("KOS_PRICING_JSON"))

    # --- embeddings --------------------------------------------------------------
    embeddings_provider: str = Field("local", validation_alias=_env("KOS_EMBEDDINGS"))  # local|openai|none
    embeddings_model: str = Field("BAAI/bge-small-en-v1.5", validation_alias=_env("KOS_EMBEDDINGS_MODEL"))
    openai_embeddings_model: str = Field("text-embedding-3-small", validation_alias=_env("KOS_OPENAI_EMBEDDINGS_MODEL"))

    # --- housekeeping ---------------------------------------------------------
    reconcile_interval: int = Field(30, validation_alias=_env("KOS_RECONCILE_INTERVAL"))
    backup_interval_hours: int = Field(24, validation_alias=_env("KOS_BACKUP_INTERVAL_HOURS"))
    backup_keep: int = Field(14, validation_alias=_env("KOS_BACKUP_KEEP"))
    versions_keep: int = Field(50, validation_alias=_env("KOS_VERSIONS_KEEP"))
    max_upload_mb: int = Field(25, validation_alias=_env("KOS_MAX_UPLOAD_MB"))
    allow_private_urls: bool = Field(False, validation_alias=_env("KOS_ALLOW_PRIVATE_URLS"))
    background_workers: bool = Field(True, validation_alias=_env("KOS_BACKGROUND_WORKERS"))
    log_level: str = Field("INFO", validation_alias=_env("KOS_LOG_LEVEL"))
    frontend_dist: Path | None = Field(None, validation_alias=_env("KOS_FRONTEND_DIST"))

    # --- derived paths ------------------------------------------------------------
    @property
    def data_dir(self) -> Path:
        return (self.data_dir_override or (self.root / "data")).resolve()

    @property
    def vault_dir(self) -> Path:
        return self.data_dir / "vault"

    @property
    def state_dir(self) -> Path:
        return self.data_dir / ".brainbox"

    @property
    def db_path(self) -> Path:
        return self.state_dir / "knowledge.db"

    @property
    def attachments_dir(self) -> Path:
        return self.data_dir / "attachments"

    @property
    def backups_dir(self) -> Path:
        return self.data_dir / "backups"

    @property
    def trash_dir(self) -> Path:
        return self.state_dir / "trash"

    @property
    def models_dir(self) -> Path:
        return self.models_dir_override or (self.state_dir / "models")

    @property
    def auth_file(self) -> Path:
        return self.state_dir / "auth.json"

    @property
    def resolved_agent_model(self) -> str:
        return self.agent_model or self.chat_model

    @property
    def resolved_utility_model(self) -> str:
        return self.utility_model or self.chat_model

    @property
    def allowed_hosts(self) -> list[str]:
        hosts = {"localhost", "127.0.0.1"}
        for raw in [self.mcp_public_base, *self.mcp_allowed_hosts.split(",")]:
            raw = raw.strip()
            if not raw:
                continue
            host = raw.split("://", 1)[-1].split("/", 1)[0].split(":", 1)[0]
            if host:
                hosts.add(host)
        out: list[str] = []
        for h in sorted(hosts):
            out += [h, f"{h}:*"]
        return out

    def ensure_dirs(self) -> None:
        for folder in ("Notes", "Projects", "Resources", "Skills", "Agents", "Inbox", "Attachments"):
            (self.vault_dir / folder).mkdir(parents=True, exist_ok=True)
        for p in (self.state_dir, self.attachments_dir, self.backups_dir, self.trash_dir):
            p.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.ensure_dirs()
    return s


def reset_settings() -> None:
    """Used by tests after changing environment variables."""
    get_settings.cache_clear()
