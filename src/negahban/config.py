"""Configuration loaded from ``.env`` / the process environment.

Secrets are not configuration. ``.env`` holds ids and *where* the secrets are
(gopass entries); the secrets themselves are fetched through
:mod:`negahban.secrets`, and only by the commands that need them. Plain env
vars still work for one-off runs.
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

# Where Instagram sends the browser after login. A static page that shows the
# code to paste back into the terminal; it must be registered as a redirect
# URI of the Meta app. Override with NEGAHBAN_REDIRECT_URI.
DEFAULT_REDIRECT_URI = "https://elaheh-dastan.github.io/negahban/callback/"


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or malformed."""


@dataclass(frozen=True, slots=True)
class Settings:
    """Everything the CLI needs that is not a flag."""

    ig_app_id: str
    token_store: TokenStore
    model: str
    redirect_uri: str = DEFAULT_REDIRECT_URI
    allowlist: frozenset[str] = field(default_factory=frozenset)
    _app_secret: str = field(default="", repr=False)
    _app_secret_entry: str = field(default="", repr=False)

    _anthropic_key_entry: str = field(default="", repr=False)

    def app_secret(self) -> str:
        """The Instagram app secret, fetched from gopass only when asked for.

        Only ``auth``/``login`` need it (to exchange a token), so a scan or an
        unhide never pays for a gopass call — or a passphrase prompt.
        """
        if self._app_secret:
            return self._app_secret
        return _from_gopass(
            self._app_secret_entry,
            "Set IG_APP_SECRET, or NEGAHBAN_GOPASS_APP_SECRET to the gopass entry holding "
            "the Instagram app secret. See .env.example.",
        )

    def anthropic_api_key(self) -> str | None:
        """The Claude API key from gopass, or ``None`` to let the SDK resolve it
        from ``ANTHROPIC_API_KEY`` / ``ANTHROPIC_AUTH_TOKEN`` itself."""
        if not self._anthropic_key_entry:
            return None
        return _from_gopass(self._anthropic_key_entry, "")


def _from_gopass(entry: str, missing_message: str) -> str:
    if not entry:
        raise ConfigError(missing_message)
    try:
        return gopass_show(entry)[0].strip()
    except SecretError as error:
        raise ConfigError(str(error)) from error


def _parse_allowlist(raw: str) -> frozenset[str]:
    """Lowercased usernames from a comma-separated string; ``@`` and blanks dropped."""
    names = {item.strip().lstrip("@").lower() for item in raw.split(",")}
    return frozenset(name for name in names if name)


def _env(name: str) -> str:
    return os.getenv(name, "").strip()


def _token_store(token_file: Path) -> TokenStore:
    """gopass when ``NEGAHBAN_GOPASS_TOKEN`` names an entry, else ``token_file``."""
    entry = _env("NEGAHBAN_GOPASS_TOKEN")
    return GopassTokenStore(entry) if entry else FileTokenStore(token_file)


def load_settings(token_file: Path = TOKEN_FILE) -> Settings:
    """Load settings from ``.env`` (or the environment).

    Raises:
        ConfigError: if ``IG_APP_ID`` is missing.
    """
    load_dotenv()
    app_id = _env("IG_APP_ID")
    if not app_id:
        raise ConfigError("IG_APP_ID must be set. Copy .env.example to .env and fill it in.")
    return Settings(
        ig_app_id=app_id,
        token_store=_token_store(token_file),
        model=_env("NEGAHBAN_MODEL") or DEFAULT_MODEL,
        redirect_uri=_env("NEGAHBAN_REDIRECT_URI") or DEFAULT_REDIRECT_URI,
        allowlist=_parse_allowlist(_env("NEGAHBAN_ALLOWLIST")),
        _app_secret=_env("IG_APP_SECRET"),
        _app_secret_entry=_env("NEGAHBAN_GOPASS_APP_SECRET"),
        _anthropic_key_entry=_env("NEGAHBAN_GOPASS_ANTHROPIC_KEY"),
    )
