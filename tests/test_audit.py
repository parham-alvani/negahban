from datetime import UTC, datetime
from pathlib import Path

from negahban.audit import AuditLog
from negahban.models import Action, Comment, Decision, Label, Verdict


def _decision(comment_id: str, action: Action) -> Decision:
    comment = Comment(
        comment_id=comment_id,
        media_id="m",
        username="someone",
        text="hello",
        timestamp=datetime.now(UTC),
        hidden=False,
    )
    return Decision(comment, Verdict(Label.INSULT, 0.9, "rude"), action)


def test_manual_action_on_unknown_comment_adds_a_manual_row(tmp_path: Path) -> None:
    with AuditLog(tmp_path / "a.db") as log:
        log.record_manual("42", Action.HIDE)
        row = log.get("42")
    assert row is not None
    assert (row.label, row.action, row.applied) == ("manual", "hide", True)


def test_manual_action_on_judged_comment_keeps_the_verdict(tmp_path: Path) -> None:
    with AuditLog(tmp_path / "a.db") as log:
        log.record_manual("42", Action.NONE)  # a bare unhide first
        log.record(_decision("42", Action.HIDE))
        log.record_manual("42", Action.HIDE)
        row = log.get("42")
        assert "42" in log.seen()
    assert row is not None
    assert (row.label, row.action, row.applied, row.reason) == ("insult", "hide", True, "rude")
