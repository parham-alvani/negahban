"""Append-only record of every judged comment, in a local SQLite file.

Two jobs: make runs idempotent (a comment judged once is skipped next time,
so repeated scans only pay for new comments) and leave a trail you can audit —
what the model said, why, what was done, and when — so a wrong hide can be
found and reversed with ``negahban unhide``.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from negahban.models import Action, Decision

_SCHEMA = """
CREATE TABLE IF NOT EXISTS decisions (
    comment_id   TEXT PRIMARY KEY,
    media_id     TEXT NOT NULL,
    username     TEXT NOT NULL,
    text         TEXT NOT NULL,
    label        TEXT NOT NULL,
    confidence   REAL NOT NULL,
    reason       TEXT NOT NULL,
    action       TEXT NOT NULL,
    applied      INTEGER NOT NULL DEFAULT 0,
    judged_at    TEXT NOT NULL,
    applied_at   TEXT
);
"""


@dataclass(frozen=True, slots=True)
class AuditRow:
    comment_id: str
    media_id: str
    username: str
    text: str
    label: str
    confidence: float
    reason: str
    action: str
    applied: bool
    judged_at: str
    applied_at: str | None


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class AuditLog:
    """SQLite-backed decision log. Use as a context manager."""

    def __init__(self, path: Path) -> None:
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> AuditLog:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def seen(self) -> frozenset[str]:
        """Ids of every comment already judged."""
        rows = self._conn.execute("SELECT comment_id FROM decisions").fetchall()
        return frozenset(str(row["comment_id"]) for row in rows)

    def record(self, decision: Decision) -> None:
        """Insert or replace the judgement for a comment (not yet applied)."""
        comment, verdict = decision.comment, decision.verdict
        self._conn.execute(
            """
            INSERT OR REPLACE INTO decisions
                (comment_id, media_id, username, text, label, confidence, reason,
                 action, applied, judged_at, applied_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, NULL)
            """,
            (
                comment.comment_id,
                comment.media_id,
                comment.username,
                comment.text,
                verdict.label.value,
                verdict.confidence,
                verdict.reason,
                decision.action.value,
                _now(),
            ),
        )
        self._conn.commit()

    def mark_applied(self, comment_id: str, action: Action) -> None:
        """Note that ``action`` was carried out on ``comment_id`` just now."""
        self._conn.execute(
            "UPDATE decisions SET action = ?, applied = 1, applied_at = ? WHERE comment_id = ?",
            (action.value, _now(), comment_id),
        )
        self._conn.commit()

    def record_manual(self, comment_id: str, action: Action) -> None:
        """Log an action done by hand (``negahban hide``/``unhide``) on a comment.

        A comment the classifier has judged keeps its verdict and just gets the
        new action; one it has never seen gets a row with a ``manual`` label,
        so the log is a complete trail of what negahban did to the account.
        """
        if self.get(comment_id) is not None:
            self.mark_applied(comment_id, action)
            return
        now = _now()
        self._conn.execute(
            """
            INSERT INTO decisions
                (comment_id, media_id, username, text, label, confidence, reason,
                 action, applied, judged_at, applied_at)
            VALUES (?, '', '', '', 'manual', 1.0, 'done by hand', ?, 1, ?, ?)
            """,
            (comment_id, action.value, now, now),
        )
        self._conn.commit()

    def pending(self) -> list[AuditRow]:
        """Decisions whose action is hide/delete and has not been applied yet."""
        rows = self._conn.execute(
            "SELECT * FROM decisions WHERE applied = 0 AND action IN ('hide', 'delete') "
            "ORDER BY judged_at"
        ).fetchall()
        return [_row(row) for row in rows]

    def recent(self, limit: int) -> list[AuditRow]:
        rows = self._conn.execute(
            "SELECT * FROM decisions ORDER BY judged_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [_row(row) for row in rows]

    def get(self, comment_id: str) -> AuditRow | None:
        row = self._conn.execute(
            "SELECT * FROM decisions WHERE comment_id = ?", (comment_id,)
        ).fetchone()
        return _row(row) if row is not None else None


def _row(row: sqlite3.Row) -> AuditRow:
    return AuditRow(
        comment_id=str(row["comment_id"]),
        media_id=str(row["media_id"]),
        username=str(row["username"]),
        text=str(row["text"]),
        label=str(row["label"]),
        confidence=float(row["confidence"]),
        reason=str(row["reason"]),
        action=str(row["action"]),
        applied=bool(row["applied"]),
        judged_at=str(row["judged_at"]),
        applied_at=str(row["applied_at"]) if row["applied_at"] is not None else None,
    )
