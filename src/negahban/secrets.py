"""Where secrets live: gopass by default, plain files as the fallback.

The app secret and the access token never need to sit in ``.env`` or in a
JSON file next to the code. With ``NEGAHBAN_GOPASS_*`` set they are read from,
and (for the token, which negahban refreshes) written back to, the user's
gopass store. The entry layout follows gopass convention: the first line is
the secret itself, the lines after it are ``key: value`` metadata, possibly
mixed with free text that we must leave alone.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Protocol

from negahban.graph import Token

EXPIRES_KEY = "expires_at"


class SecretError(RuntimeError):
    """gopass is missing, the entry is unreadable, or it could not be written."""


class SecretNotFound(SecretError):
    """The gopass entry (or token file) simply does not exist yet."""


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
        if "not in the password store" in detail or "not found" in detail:
            raise SecretNotFound(f"gopass entry {args[-1]} does not exist")
        raise SecretError(f"gopass {args[0]} {args[-1]} failed: {detail}")
    return completed.stdout


def gopass_show(entry: str) -> list[str]:
    """Return the raw lines of a gopass entry; the first one is the secret."""
    lines = _run_gopass("show", "-f", "-n", entry).splitlines()
    if not lines or not lines[0].strip():
        raise SecretError(f"gopass entry {entry} is empty")
    return lines


def gopass_insert(entry: str, lines: list[str]) -> None:
    """Overwrite a gopass entry with exactly ``lines``."""
    _run_gopass("insert", "-f", entry, stdin="\n".join(lines) + "\n")


def metadata(lines: list[str]) -> dict[str, str]:
    """The ``key: value`` pairs among an entry's non-secret lines."""
    found: dict[str, str] = {}
    for line in lines[1:]:
        key, sep, value = line.partition(":")
        if sep and key.strip() and " " not in key.strip():
            found.setdefault(key.strip(), value.strip())
    return found


def with_expiry(lines: list[str], secret: str, expires_at: datetime) -> list[str]:
    """``lines`` with the secret replaced and ``expires_at`` set, all else untouched.

    The ``expires_at:`` line is patched in place when present and appended
    otherwise, so free text, blank lines, and other keys survive the rewrite.
    """
    stamped = f"{EXPIRES_KEY}: {expires_at.isoformat()}"
    rest = lines[1:]
    for index, line in enumerate(rest):
        if line.partition(":")[0].strip() == EXPIRES_KEY:
            rest[index] = stamped
            break
    else:
        rest.append(stamped)
    return [secret, *rest]


def _parse_expiry(raw: str | None, where: str) -> datetime | None:
    if raw is None:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError as error:
        raise SecretError(f"{where}: cannot parse {EXPIRES_KEY} {raw!r}") from error


class TokenStore(Protocol):
    """Somewhere a ``Token`` can be kept between runs."""

    def load(self) -> Token | None:
        """The stored token, or ``None`` when nothing is stored yet.

        Raises ``SecretError`` for anything other than "not there".
        """
        ...

    def save(self, token: Token) -> None: ...

    def describe(self) -> str: ...


@dataclass(slots=True)
class FileTokenStore:
    """``token.json`` next to the code. Fine for a throwaway, not for a laptop."""

    path: Path

    def load(self) -> Token | None:
        if not self.path.exists():
            return None
        data = json.loads(self.path.read_text(encoding="utf-8"))
        return Token(
            access_token=str(data["access_token"]),
            expires_at=_parse_expiry(data.get(EXPIRES_KEY), str(self.path)),
        )

    def save(self, token: Token) -> None:
        payload = {
            "access_token": token.access_token,
            EXPIRES_KEY: token.expires_at.isoformat() if token.expires_at else None,
        }
        # Owner-only from the first byte: the file holds a 60-day credential.
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)

    def describe(self) -> str:
        return str(self.path)


@dataclass(slots=True)
class GopassTokenStore:
    """A gopass entry: the token on the first line, ``expires_at`` in the metadata.

    Everything else in the entry (username, scopes, notes) is preserved on save,
    so it stays readable by a human as well as by negahban.
    """

    entry: str
    _lines: list[str] | None = field(default=None, repr=False)

    def load(self) -> Token | None:
        try:
            self._lines = gopass_show(self.entry)
        except SecretNotFound:
            return None
        expires_raw = metadata(self._lines).get(EXPIRES_KEY)
        return Token(
            access_token=self._lines[0].strip(),
            expires_at=_parse_expiry(expires_raw, self.describe()),
        )

    def save(self, token: Token) -> None:
        if self._lines is None:
            try:
                self._lines = gopass_show(self.entry)
            except SecretNotFound:
                self._lines = [token.access_token]
        if token.expires_at is None:
            self._lines = [token.access_token, *self._lines[1:]]
        else:
            self._lines = with_expiry(self._lines, token.access_token, token.expires_at)
        gopass_insert(self.entry, self._lines)

    def describe(self) -> str:
        return f"gopass:{self.entry}"
