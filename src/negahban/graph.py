"""Instagram API (with Instagram Login) client — the only module that knows HTTP.

Everything here is official, documented Graph API: reading the account's own
media and comments, and hiding/unhiding/deleting comments. Requires a
professional (Business/Creator) Instagram account and a user token carrying
``instagram_business_basic`` and ``instagram_business_manage_comments``.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from negahban.config import GRAPH_VERSION
from negahban.models import Comment, Media

GRAPH_BASE = f"https://graph.instagram.com/{GRAPH_VERSION}"
TOKEN_BASE = "https://graph.instagram.com"

# Refresh when fewer than this many days remain; tokens live 60 days and can
# only be refreshed once they are at least 24 hours old.
REFRESH_WITHIN = timedelta(days=7)

_COMMENT_FIELDS = "id,text,timestamp,username,hidden"
_REPLY_FIELDS = f"replies{{{_COMMENT_FIELDS}}}"


class GraphError(RuntimeError):
    """A Graph API call failed; the message is Meta's own error text."""


@dataclass(frozen=True, slots=True)
class Token:
    """A long-lived user access token and when it stops working."""

    access_token: str
    expires_at: datetime

    @property
    def needs_refresh(self) -> bool:
        return datetime.now(UTC) + REFRESH_WITHIN >= self.expires_at

    def save(self, path: Path) -> None:
        path.write_text(
            json.dumps(
                {"access_token": self.access_token, "expires_at": self.expires_at.isoformat()}
            ),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, path: Path) -> Token | None:
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            access_token=str(data["access_token"]),
            expires_at=datetime.fromisoformat(str(data["expires_at"])),
        )


def _raise_for_graph_error(response: httpx.Response) -> dict[str, Any]:
    """Return the JSON body, raising ``GraphError`` with Meta's message on failure."""
    try:
        body = response.json()
    except ValueError as error:
        raise GraphError(f"HTTP {response.status_code}: {response.text[:200]}") from error
    if response.is_error or "error" in body:
        detail = body.get("error", {}) if isinstance(body, dict) else {}
        message = detail.get("message") or response.text[:200]
        code = detail.get("code", response.status_code)
        raise GraphError(f"Graph API error {code}: {message}")
    return body


def _parse_timestamp(raw: str) -> datetime:
    # Graph returns e.g. "2024-05-01T12:34:56+0000" — not quite ISO 8601.
    return datetime.strptime(raw, "%Y-%m-%dT%H:%M:%S%z")


def exchange_for_long_lived(app_secret: str, short_lived_token: str) -> Token:
    """Turn a short-lived (1h) token into a long-lived (60d) one."""
    response = httpx.get(
        f"{TOKEN_BASE}/access_token",
        params={
            "grant_type": "ig_exchange_token",
            "client_secret": app_secret,
            "access_token": short_lived_token,
        },
        timeout=30,
    )
    body = _raise_for_graph_error(response)
    return Token(
        access_token=str(body["access_token"]),
        expires_at=datetime.now(UTC) + timedelta(seconds=int(body["expires_in"])),
    )


def refresh_long_lived(token: Token) -> Token:
    """Extend a long-lived token for another 60 days."""
    response = httpx.get(
        f"{TOKEN_BASE}/refresh_access_token",
        params={"grant_type": "ig_refresh_token", "access_token": token.access_token},
        timeout=30,
    )
    body = _raise_for_graph_error(response)
    return Token(
        access_token=str(body["access_token"]),
        expires_at=datetime.now(UTC) + timedelta(seconds=int(body["expires_in"])),
    )


class InstagramClient:
    """Read the account's posts and comments; hide, unhide, or delete comments."""

    def __init__(self, token: Token) -> None:
        self._token = token
        self._http = httpx.Client(base_url=GRAPH_BASE, timeout=30)

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> InstagramClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # -- low level -----------------------------------------------------------

    def _get(self, path: str, **params: str | int) -> dict[str, Any]:
        response = self._http.get(path, params={**params, "access_token": self._token.access_token})
        return _raise_for_graph_error(response)

    def _post(self, path: str, **params: str | int | bool) -> dict[str, Any]:
        response = self._http.post(path, data={**params, "access_token": self._token.access_token})
        return _raise_for_graph_error(response)

    def _delete(self, path: str) -> dict[str, Any]:
        response = self._http.delete(path, params={"access_token": self._token.access_token})
        return _raise_for_graph_error(response)

    def _paginate(self, path: str, **params: str | int) -> Iterator[dict[str, Any]]:
        """Yield every item of a paged edge, following ``paging.next``."""
        body = self._get(path, **params)
        while True:
            yield from body.get("data", [])
            next_url = body.get("paging", {}).get("next")
            if not next_url:
                return
            # ``next`` is absolute and already carries the token and params.
            body = _raise_for_graph_error(self._http.get(next_url))

    # -- account & media -----------------------------------------------------

    def me(self) -> dict[str, Any]:
        """``{user_id, username, account_type}`` of the token's account."""
        return self._get("/me", fields="user_id,username,account_type")

    def recent_media(self, limit: int) -> list[Media]:
        """The account's most recent posts, newest first; ``0`` means all."""
        medias: list[Media] = []
        for item in self._paginate(
            "/me/media",
            fields="id,caption,permalink,timestamp,comments_count",
            limit=min(limit, 50) if limit else 50,
        ):
            medias.append(
                Media(
                    media_id=str(item["id"]),
                    caption=str(item.get("caption") or ""),
                    permalink=str(item.get("permalink") or ""),
                    timestamp=_parse_timestamp(str(item["timestamp"])),
                    comments_count=int(item.get("comments_count") or 0),
                )
            )
            if limit and len(medias) >= limit:
                break
        return medias

    # -- comments ------------------------------------------------------------

    def comments(self, media: Media) -> list[Comment]:
        """Every top-level comment on ``media`` and its replies, flattened."""
        found: list[Comment] = []
        for item in self._paginate(
            f"/{media.media_id}/comments",
            fields=f"{_COMMENT_FIELDS},{_REPLY_FIELDS}",
            limit=50,
        ):
            parent = self._comment_from_item(item, media.media_id, parent_id=None)
            found.append(parent)
            for reply in item.get("replies", {}).get("data", []):
                found.append(
                    self._comment_from_item(reply, media.media_id, parent_id=parent.comment_id)
                )
        return found

    @staticmethod
    def _comment_from_item(
        item: dict[str, Any], media_id: str, *, parent_id: str | None
    ) -> Comment:
        return Comment(
            comment_id=str(item["id"]),
            media_id=media_id,
            # ``username`` is absent for comments by deactivated/blocked users.
            username=str(item.get("username") or ""),
            text=str(item.get("text") or ""),
            timestamp=_parse_timestamp(str(item["timestamp"])),
            hidden=bool(item.get("hidden", False)),
            parent_id=parent_id,
        )

    def hide(self, comment_id: str) -> None:
        self._post(f"/{comment_id}", hide=True)

    def unhide(self, comment_id: str) -> None:
        self._post(f"/{comment_id}", hide=False)

    def delete(self, comment_id: str) -> None:
        self._delete(f"/{comment_id}")
