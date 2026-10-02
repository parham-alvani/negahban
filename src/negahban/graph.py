"""Instagram API (with Instagram Login) client — the only module that knows HTTP.

Everything here is official, documented Graph API: reading the account's own
media and comments, and hiding/unhiding/deleting comments. Requires a
professional (Business/Creator) Instagram account and a user token carrying
``instagram_business_basic`` and ``instagram_business_manage_comments``.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from negahban.models import Comment, Media

# Graph API version negahban talks to. Versions live for about two years; bump
# deliberately and re-check the comment fields when you do.
GRAPH_VERSION = "v23.0"
GRAPH_BASE = f"https://graph.instagram.com/{GRAPH_VERSION}"
TOKEN_BASE = "https://graph.instagram.com"
AUTHORIZE_URL = "https://www.instagram.com/oauth/authorize"
CODE_EXCHANGE_URL = "https://api.instagram.com/oauth/access_token"

# The permissions negahban asks for at login: read the account and its media,
# and read/hide/delete comments. Nothing else.
SCOPES = ("instagram_business_basic", "instagram_business_manage_comments")

# Refresh when fewer than this many days remain; tokens live 60 days.
REFRESH_WITHIN = timedelta(days=7)

# ``username`` is only filled in for the account owner's own comments; other
# people's show up under ``from``. Ask for both and prefer ``from``.
_COMMENT_FIELDS = "id,text,timestamp,username,from,hidden"
_REPLY_FIELDS = f"replies{{{_COMMENT_FIELDS}}}"


class GraphError(RuntimeError):
    """A Graph API call failed; the message is Meta's own error text."""


@dataclass(frozen=True, slots=True)
class Token:
    """A long-lived user access token and, when known, when it stops working.

    ``expires_at`` is ``None`` for a token pasted in by hand: Meta has no
    endpoint that reports a token's expiry, so it is only learned by
    refreshing, which hands back a fresh token with a known lifetime.
    """

    access_token: str
    expires_at: datetime | None = None

    @property
    def needs_refresh(self) -> bool:
        if self.expires_at is None:
            return True
        return datetime.now(UTC) + REFRESH_WITHIN >= self.expires_at


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


def authorize_url(app_id: str, redirect_uri: str) -> str:
    """The Instagram business-login page where the account owner grants access."""
    query = httpx.QueryParams(
        {
            "client_id": app_id,
            "redirect_uri": redirect_uri,
            "scope": ",".join(SCOPES),
            "response_type": "code",
        }
    )
    return f"{AUTHORIZE_URL}?{query}"


def exchange_code(app_id: str, app_secret: str, redirect_uri: str, code: str) -> str:
    """Turn the ``code`` from the login redirect into a short-lived (1h) token.

    Instagram appends ``#_`` to the redirected URL; a code pasted with that
    suffix is accepted here and cleaned up.
    """
    response = httpx.post(
        CODE_EXCHANGE_URL,
        data={
            "client_id": app_id,
            "client_secret": app_secret,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
            "code": code.strip().removesuffix("#_"),
        },
        timeout=30,
    )
    body = _raise_for_graph_error(response)
    return str(body["access_token"])


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
    """Extend a long-lived token for another 60 days.

    Also the way to learn the expiry of a long-lived token obtained elsewhere:
    the dashboard's token generator hands out long-lived ones, which
    ``ig_exchange_token`` rejects, and refreshing works on them right away.
    """
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
        """Every comment on ``media``, replies included, each exactly once."""
        items = list(
            self._paginate(
                f"/{media.media_id}/comments",
                fields=f"{_COMMENT_FIELDS},{_REPLY_FIELDS}",
                limit=50,
            )
        )
        return flatten_comments(items, media.media_id)

    @staticmethod
    def _comment_from_item(
        item: dict[str, Any], media_id: str, *, parent_id: str | None
    ) -> Comment:
        return _comment_from_item(item, media_id, parent_id=parent_id)


def flatten_comments(items: list[dict[str, Any]], media_id: str) -> list[Comment]:
    """Turn the raw comments edge into one ``Comment`` per id.

    Instagram lists every reply twice: nested under its parent's ``replies``
    and again as a top-level item. The nested copy is the useful one (it knows
    its parent), so replies are collected first and the top-level pass skips
    anything already seen. Order follows the top-level list, replies right
    after their parent.
    """
    by_id: dict[str, Comment] = {}
    for item in items:
        for reply in item.get("replies", {}).get("data", []):
            comment = _comment_from_item(reply, media_id, parent_id=str(item["id"]))
            by_id.setdefault(comment.comment_id, comment)

    ordered: list[Comment] = []
    emitted: set[str] = set()
    for item in items:
        comment_id = str(item["id"])
        if comment_id in emitted:
            continue
        comment = by_id.get(comment_id) or _comment_from_item(item, media_id, parent_id=None)
        ordered.append(comment)
        emitted.add(comment_id)
        for reply in item.get("replies", {}).get("data", []):
            reply_id = str(reply["id"])
            if reply_id not in emitted:
                ordered.append(by_id[reply_id])
                emitted.add(reply_id)
    return ordered


def _comment_from_item(item: dict[str, Any], media_id: str, *, parent_id: str | None) -> Comment:
        author = item.get("from") or {}
        return Comment(
            comment_id=str(item["id"]),
            media_id=media_id,
            # Both are absent for comments by deactivated/blocked users.
            username=str(author.get("username") or item.get("username") or ""),
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
