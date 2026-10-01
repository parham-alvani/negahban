"""Configuration loaded from ``.env`` / the process environment."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

TOKEN_FILE = Path("token.json")
AUDIT_DB = Path("negahban.db")

DEFAULT_MODEL = "claude-opus-5"
DEFAULT_MAX_POSTS = 10

# Graph API version negahban talks to. Versions live for about two years; bump
# deliberately and re-check the comment fields when you do.
GRAPH_VERSION = "v23.0"


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or malformed."""


@dataclass(frozen=True, slots=True)
class Settings:
    """Everything the CLI needs that is not a flag."""

    ig_app_id: str
    ig_app_secret: str
    initial_access_token: str | None
    model: str
    allowlist: frozenset[str] = field(default_factory=frozenset)


def _parse_allowlist(raw: str) -> frozenset[str]:
    """Lowercased usernames from a comma-separated string; ``@`` and blanks dropped."""
    names = {item.strip().lstrip("@").lower() for item in raw.split(",")}
    return frozenset(name for name in names if name)


def load_settings() -> Settings:
    """Load settings from ``.env`` (or the environment).

    Raises:
        ConfigError: if ``IG_APP_ID`` or ``IG_APP_SECRET`` is missing.
    """
    load_dotenv()
    app_id = os.getenv("IG_APP_ID", "").strip()
    app_secret = os.getenv("IG_APP_SECRET", "").strip()
    if not app_id or not app_secret:
        raise ConfigError(
            "IG_APP_ID and IG_APP_SECRET must be set. "
            "Copy .env.example to .env and fill in the Instagram app credentials."
        )
    token = os.getenv("IG_ACCESS_TOKEN", "").strip() or None
    return Settings(
        ig_app_id=app_id,
        ig_app_secret=app_secret,
        initial_access_token=token,
        model=os.getenv("NEGAHBAN_MODEL", "").strip() or DEFAULT_MODEL,
        allowlist=_parse_allowlist(os.getenv("NEGAHBAN_ALLOWLIST", "")),
    )
