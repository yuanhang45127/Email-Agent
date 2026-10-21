"""Configuration loading for MailRadar.

Configuration comes from a YAML file (see ``config.example.yaml``) with
sensitive values (IMAP password) supplied via environment variable or a
local ``.env`` file that is never committed.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class ConfigError(Exception):
    """Raised when configuration is missing or malformed."""


@dataclass
class ImapConfig:
    host: str
    port: int = 993
    user: str = ""
    password: str = ""
    # Folder name -> direction tag used when archiving.
    folders: list[tuple[str, str]] = field(
        default_factory=lambda: [("INBOX", "in"), ('"Sent Items"', "out")]
    )


@dataclass
class ContactsConfig:
    # Glob patterns (relative to project root) pointing at CSV contact tables.
    sources: list[str] = field(default_factory=lambda: ["contacts/*.csv"])
    # Maps internal field names to the CSV column headers in use.
    columns: dict[str, str] = field(
        default_factory=lambda: {
            "email": "email",
            "name": "name",
            "org": "org",
            "dir": "dir",
            "sent": "sent",
            "replied": "replied",
            "status": "status",
        }
    )
    # Cell value that marks a boolean column as true.
    truthy: str = "是"


@dataclass
class ArchiveConfig:
    out_dir: str = "archive"
    state_file: str = ".mailradar_state.json"


@dataclass
class NtfyConfig:
    topic: str = ""
    auth_file: str = ""


@dataclass
class WeixinConfig:
    enabled: bool = False
    channel: str = "openclaw-weixin"
    to: str = ""


@dataclass
class NotifyConfig:
    channels: list[str] = field(default_factory=lambda: ["ntfy"])
    ntfy: NtfyConfig = field(default_factory=NtfyConfig)
    weixin: WeixinConfig = field(default_factory=WeixinConfig)


@dataclass
class Config:
    root: Path
    imap: ImapConfig
    contacts: ContactsConfig
    archive: ArchiveConfig
    notify: NotifyConfig
    self_addresses: list[str] = field(default_factory=list)

    def resolve(self, rel: str) -> Path:
        """Resolve a project-relative path against the configured root."""
        return (self.root / rel).resolve()


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        import yaml  # type: ignore
    except ImportError as exc:  # pragma: no cover - dependency hint
        raise ConfigError("PyYAML is required: pip install pyyaml") from exc
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ConfigError(f"{path} must contain a YAML mapping at the top level")
    return data


def _load_dotenv(root: Path) -> dict[str, str]:
    """Minimal .env reader (KEY=VALUE lines, ``#`` comments)."""
    env: dict[str, str] = {}
    env_file = root / ".env"
    if not env_file.is_file():
        return env
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        env[key.strip()] = val.strip().strip('"').strip("'")
    return env


def _get(env: dict[str, str], key: str, default: str = "") -> str:
    """Environment variable wins, then .env, then default."""
    return os.environ.get(key) or env.get(key) or default


def load_config(path: str | Path | None = None) -> Config:
    """Load configuration from ``path`` (default ``config.yaml`` under root).

    The project root is the directory containing the config file.
    """
    if path is None:
        path = Path.cwd() / "config.yaml"
    path = Path(path)
    if not path.is_file():
        raise ConfigError(f"config file not found: {path}")
    root = path.resolve().parent
    raw = _load_yaml(path)
    env = _load_dotenv(root)

    imap_raw = raw.get("imap", {})
    imap = ImapConfig(
        host=imap_raw.get("host", ""),
        port=int(imap_raw.get("port", 993)),
        user=imap_raw.get("user", ""),
        password=_get(env, "MAILRADAR_IMAP_PASSWORD"),
        folders=[(f[0], f[1]) for f in imap_raw.get("folders", [["INBOX", "in"], ['"Sent Items"', "out"]])],
    )
    if not imap.host:
        raise ConfigError("imap.host is required")
    if not imap.password:
        raise ConfigError("IMAP password missing: set MAILRADAR_IMAP_PASSWORD or .env")

    contacts_raw = raw.get("contacts", {})
    contacts = ContactsConfig(
        sources=contacts_raw.get("sources", ["contacts/*.csv"]),
        columns={**ContactsConfig().columns, **contacts_raw.get("columns", {})},
        truthy=contacts_raw.get("truthy", "是"),
    )

    archive_raw = raw.get("archive", {})
    archive = ArchiveConfig(
        out_dir=archive_raw.get("out_dir", "archive"),
        state_file=archive_raw.get("state_file", ".mailradar_state.json"),
    )

    notify_raw = raw.get("notify", {})
    ntfy_raw = notify_raw.get("ntfy", {})
    weixin_raw = notify_raw.get("weixin", {})
    notify = NotifyConfig(
        channels=notify_raw.get("channels", ["ntfy"]),
        ntfy=NtfyConfig(
            topic=ntfy_raw.get("topic", ""),
            auth_file=ntfy_raw.get("auth_file", ""),
        ),
        weixin=WeixinConfig(
            enabled=bool(weixin_raw.get("enabled", False)),
            channel=weixin_raw.get("channel", "openclaw-weixin"),
            to=weixin_raw.get("to", ""),
        ),
    )

    self_addresses = [a.lower() for a in raw.get("self_addresses", [])]

    return Config(
        root=root,
        imap=imap,
        contacts=contacts,
        archive=archive,
        notify=notify,
        self_addresses=self_addresses,
    )


_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def extract_emails(text: str | None) -> list[str]:
    """Pull every email address out of a free-form cell (lower-cased)."""
    if not text:
        return []
    return [m.group(0).lower() for m in _EMAIL_RE.finditer(text)]
