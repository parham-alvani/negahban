"""Typed domain models — the library-agnostic core the rest of the code speaks."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class Label(StrEnum):
    """What a comment is, as judged by the classifier.

    ``NEGATIVE_LEGIT`` is deliberately separate from the abusive labels: a
    critical-but-civil comment is feedback, not abuse, and hiding it makes a
    page look dishonest. The default policy only flags it for a human.
    """

    OK = "ok"
    NEGATIVE_LEGIT = "negative_legit"
    INSULT = "insult"
    HARASSMENT = "harassment"
    HATE = "hate"
    SEXUAL = "sexual"
    SPAM = "spam"
    SCAM = "scam"


class Action(StrEnum):
    """What the policy decided to do about a comment."""

    NONE = "none"
    FLAG = "flag"
    HIDE = "hide"
    DELETE = "delete"


@dataclass(frozen=True, slots=True)
class Media:
    """One of the account's own posts."""

    media_id: str
    caption: str
    permalink: str
    timestamp: datetime
    comments_count: int


@dataclass(frozen=True, slots=True)
class Comment:
    """A comment (or reply) on one of the account's posts."""

    comment_id: str
    media_id: str
    username: str
    text: str
    timestamp: datetime
    hidden: bool
    parent_id: str | None = None

    @property
    def is_reply(self) -> bool:
        return self.parent_id is not None


@dataclass(frozen=True, slots=True)
class Verdict:
    """The classifier's judgement of a single comment."""

    label: Label
    confidence: float
    reason: str


@dataclass(frozen=True, slots=True)
class Decision:
    """A verdict plus the action the policy derived from it."""

    comment: Comment
    verdict: Verdict
    action: Action
    allowlisted: bool = False
