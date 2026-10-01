"""Configuration loaded from ``.env`` / the process environment.

Secrets are not configuration. ``.env`` holds ids and *where* the secrets are
(gopass entries); the secrets themselves are fetched through
:mod:`negahban.secrets`. Plain env vars still work for one-off runs.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

from negahban.secrets import (
    FileTokenStore,
    GopassTokenStore,
    SecretError,
    TokenStore,
    gopass_show,
)

TOKEN_FILE = Path("token.json")
AUDIT_DB = Path("negahban.db")

DEFAULT_MODEL = "claude-opus-5"
DEFAULT_MAX_POSTS = 10


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or malformed."""


@dataclass(frozen=True, slots=True)
class Settings:
    """Everything the CLI needs that is not a flag."""

    ig_app_id: str
    ig_app_secret: str
    token_store: TokenStore
    model: str
    allowlist: frozenset[str] = field(default_factory=frozenset)


def _parse_allowlist(raw: str) -> frozenset[str]:
    """Lowercased usernames from a comma-separated string; ``@`` and blanks dropped."""
    names = {item.strip().lstrip("@").lower() for item in raw.split(",")}
    return frozenset(name for name in names if name)


def _env(name: str) -> str:
    return os.getenv(name, "").strip()


def _app_secret() -> str:
    """``IG_APP_SECRET`` from the environment, else from the gopass entry named by
    ``NEGAHBAN_GOPASS_APP_SECRET``."""
    direct = _env("IG_APP_SECRET")
    if direct:
        return direct
    entry = _env("NEGAHBAN_GOPASS_APP_SECRET")
    if not entry:
        raise ConfigError(
            "Set IG_APP_SECRET, or NEGAHBAN_GOPASS_APP_SECRET to the gopass entry holding "
            "the Instagram app secret. See .env.example."
        )
    try:
        secret, _ = gopass_show(entry)
    except SecretError as error:
        raise ConfigError(str(error)) from error
    return secret


def _token_store(token_file: Path) -> TokenStore:
    """gopass when ``NEGAHBAN_GOPASS_TOKEN`` names an entry, else ``token_file``."""
    entry = _env("NEGAHBAN_GOPASS_TOKEN")
    return GopassTokenStore(entry) if entry else FileTokenStore(token_file)


def load_settings(token_file: Path = TOKEN_FILE) -> Settings:
    """Load settings from ``.env`` (or the environment).

    Raises:
        ConfigError: if ``IG_APP_ID`` or the app secret cannot be resolved.
    """
    load_dotenv()
    app_id = _env("IG_APP_ID")
    if not app_id:
        raise ConfigError("IG_APP_ID must be set. Copy .env.example to .env and fill it in.")
    return Settings(
        ig_app_id=app_id,
        ig_app_secret=_app_secret(),
        token_store=_token_store(token_file),
        model=_env("NEGAHBAN_MODEL") or DEFAULT_MODEL,
        allowlist=_parse_allowlist(_env("NEGAHBAN_ALLOWLIST")),
    )
