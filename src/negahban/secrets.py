"""Where secrets live: gopass by default, plain files as the fallback.

The app secret and the access token never need to sit in ``.env`` or in a
JSON file next to the code. With ``NEGAHBAN_GOPASS_*`` set they are read from,
and (for the token, which negahban refreshes) written back to, the user's
gopass store. The entry layout follows gopass convention: the first line is
the secret itself, the lines after it are ``key: value`` metadata.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from negahban.graph import Token


class SecretError(RuntimeError):
    """gopass is missing, the entry does not exist, or it could not be written."""


def _run_gopass(*args: str, stdin: str | None = None) -> str:
    try:
        completed = subprocess.run(
            ["gopass", *args],
            input=stdin,
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as error:
        raise SecretError("gopass is not installed or not on PATH") from error
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise SecretError(f"gopass {args[0]} {args[-1]} failed: {detail}")
    return completed.stdout


def gopass_show(entry: str) -> tuple[str, dict[str, str]]:
    """Return ``(secret, metadata)`` of a gopass entry."""
    content = _run_gopass("show", "-f", "-n", entry)
    lines = content.splitlines()
    if not lines or not lines[0].strip():
        raise SecretError(f"gopass entry {entry} is empty")
    metadata: dict[str, str] = {}
    for line in lines[1:]:
        key, sep, value = line.partition(":")
        if sep:
            metadata[key.strip()] = value.strip()
    return lines[0].strip(), metadata


def gopass_insert(entry: str, secret: str, metadata: dict[str, str]) -> None:
    """Overwrite a gopass entry with ``secret`` and ``metadata`` lines."""
    body = "\n".join([secret, *(f"{key}: {value}" for key, value in metadata.items())]) + "\n"
    _run_gopass("insert", "-f", entry, stdin=body)


class TokenStore(Protocol):
    """Somewhere a ``Token`` can be kept between runs."""

    def load(self) -> Token | None: ...

    def save(self, token: Token) -> None: ...

    def describe(self) -> str: ...


@dataclass(frozen=True, slots=True)
class FileTokenStore:
    """``token.json`` next to the code. Fine for a throwaway, not for a laptop."""

    path: Path

    def load(self) -> Token | None:
        if not self.path.exists():
            return None
        data = json.loads(self.path.read_text(encoding="utf-8"))
        return Token(
            access_token=str(data["access_token"]),
            expires_at=datetime.fromisoformat(str(data["expires_at"])),
        )

    def save(self, token: Token) -> None:
        self.path.write_text(
            json.dumps(
                {"access_token": token.access_token, "expires_at": token.expires_at.isoformat()}
            ),
            encoding="utf-8",
        )

    def describe(self) -> str:
        return str(self.path)


@dataclass(frozen=True, slots=True)
class GopassTokenStore:
    """A gopass entry: the token on the first line, ``expires_at`` in the metadata.

    Other metadata lines (username, scopes, comments) are preserved on save, so
    the entry stays readable by a human as well as by negahban.
    """

    entry: str

    def load(self) -> Token | None:
        try:
            secret, metadata = gopass_show(self.entry)
        except SecretError:
            return None
        expires_raw = metadata.get("expires_at")
        if expires_raw is None:
            # A token pasted by hand carries no expiry; ``negahban auth`` fills it in
            # by exchanging the token, so treat it as due for refresh right away.
            return Token(access_token=secret, expires_at=datetime.min.replace(tzinfo=UTC))
        return Token(access_token=secret, expires_at=datetime.fromisoformat(expires_raw))

    def save(self, token: Token) -> None:
        metadata: dict[str, str] = {}
        try:
            _, metadata = gopass_show(self.entry)
        except SecretError:
            pass
        metadata["expires_at"] = token.expires_at.isoformat()
        gopass_insert(self.entry, token.access_token, metadata)

    def describe(self) -> str:
        return f"gopass:{self.entry}"
