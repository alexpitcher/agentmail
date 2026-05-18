from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


DEFAULT_BLOCKED_EXTENSIONS = (
    ".exe,.msi,.bat,.cmd,.ps1,.sh,.js,.cjs,.mjs,.jar,.scr,.vbs,.hta,"
    ".reg,.com,.pif,.apk,.dmg,.iso"
)


def _parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _merged_env() -> dict[str, str]:
    values: dict[str, str] = {}
    values.update(_parse_env_file(Path.cwd() / ".env"))
    values.update(_parse_env_file(Path.home() / ".config" / "agentmail" / "config.env"))
    values.update(os.environ)
    return values


def _as_bool(value: str | bool | None, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return value.lower() in {"1", "true", "yes", "on"}


def _as_list(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


@dataclass(frozen=True)
class Settings:
    storage_root: Path
    db_path: Path
    bind_host: str
    bind_port: int
    ingest_token: str
    api_token: str
    auth_disabled: bool
    allowed_senders: list[str]
    max_email_bytes: int
    max_attachment_bytes: int
    startup_mail_lookback_days: int
    blocked_extensions: set[str]
    api_url: str
    forward_copy_to: str | None
    resend_api_key: str | None
    resend_api_url: str
    resend_sync_to: str | None
    resend_sync_page_limit: int
    resend_sync_max_pages: int
    share_token_ttl_seconds: int

    @classmethod
    def load(cls) -> "Settings":
        env = _merged_env()
        storage_root = Path(env.get("AGENTMAIL_STORAGE_ROOT", "./data")).expanduser()
        db_path = Path(env.get("AGENTMAIL_DB_PATH", str(storage_root / "agentmail.db"))).expanduser()
        blocked = {
            ext if ext.startswith(".") else f".{ext}"
            for ext in _as_list(env.get("AGENTMAIL_BLOCKED_EXTENSIONS", DEFAULT_BLOCKED_EXTENSIONS))
        }
        bind_host = env.get("AGENTMAIL_BIND_HOST", "127.0.0.1")
        bind_port = int(env.get("AGENTMAIL_BIND_PORT", "8787"))
        return cls(
            storage_root=storage_root,
            db_path=db_path,
            bind_host=bind_host,
            bind_port=bind_port,
            ingest_token=env.get("AGENTMAIL_INGEST_TOKEN", "change-me"),
            api_token=env.get("AGENTMAIL_API_TOKEN", "change-me"),
            auth_disabled=_as_bool(env.get("AGENTMAIL_AUTH_DISABLED"), False),
            allowed_senders=[item.lower() for item in _as_list(env.get("AGENTMAIL_ALLOWED_SENDERS"))],
            max_email_bytes=int(env.get("AGENTMAIL_MAX_EMAIL_BYTES", "26214400")),
            max_attachment_bytes=int(env.get("AGENTMAIL_MAX_ATTACHMENT_BYTES", "26214400")),
            startup_mail_lookback_days=int(env.get("AGENTMAIL_STARTUP_MAIL_LOOKBACK_DAYS", "10")),
            blocked_extensions=blocked,
            api_url=env.get("AGENTMAIL_API_URL", f"http://127.0.0.1:{bind_port}"),
            forward_copy_to=env.get("AGENTMAIL_FORWARD_COPY_TO") or None,
            resend_api_key=env.get("AGENTMAIL_RESEND_API_KEY") or None,
            resend_api_url=env.get("AGENTMAIL_RESEND_API_URL", "https://api.resend.com"),
            resend_sync_to=env.get("AGENTMAIL_RESEND_SYNC_TO") or None,
            resend_sync_page_limit=int(env.get("AGENTMAIL_RESEND_SYNC_PAGE_LIMIT", "100")),
            resend_sync_max_pages=int(env.get("AGENTMAIL_RESEND_SYNC_MAX_PAGES", "10")),
            share_token_ttl_seconds=int(env.get("AGENTMAIL_SHARE_TOKEN_TTL_SECONDS", "1200")),
        )

    def ensure_storage_dirs(self) -> None:
        for subdir in ("raw", "emails", "exports"):
            (self.storage_root / subdir).mkdir(parents=True, exist_ok=True)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)


def get_settings() -> Settings:
    settings = Settings.load()
    settings.ensure_storage_dirs()
    return settings
