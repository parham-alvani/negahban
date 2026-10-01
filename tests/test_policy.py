from datetime import UTC, datetime

from negahban.models import Action, Comment, Label, Verdict
from negahban.policy import Policy


def _comment(username: str = "someone") -> Comment:
    return Comment(
        comment_id="1",
        media_id="m",
        username=username,
        text="...",
        timestamp=datetime.now(UTC),
        hidden=False,
    )


def test_ok_is_left_alone() -> None:
    decision = Policy().decide(_comment(), Verdict(Label.OK, 0.99, ""))
    assert decision.action is Action.NONE


def test_civil_negativity_is_only_flagged_even_when_certain() -> None:
    decision = Policy().decide(_comment(), Verdict(Label.NEGATIVE_LEGIT, 1.0, ""))
    assert decision.action is Action.FLAG


def test_confident_insult_is_hidden() -> None:
    decision = Policy().decide(_comment(), Verdict(Label.INSULT, 0.9, ""))
    assert decision.action is Action.HIDE


def test_unsure_insult_is_flagged_not_hidden() -> None:
    decision = Policy(hide_threshold=0.8).decide(_comment(), Verdict(Label.INSULT, 0.7, ""))
    assert decision.action is Action.FLAG


def test_spam_is_hidden_by_default_and_deleted_only_when_asked() -> None:
    verdict = Verdict(Label.SPAM, 0.95, "")
    assert Policy().decide(_comment(), verdict).action is Action.HIDE
    assert Policy(delete_junk=True).decide(_comment(), verdict).action is Action.DELETE


def test_allowlisted_user_is_never_touched() -> None:
    policy = Policy(allowlist=frozenset({"bestie"}))
    decision = policy.decide(_comment("Bestie"), Verdict(Label.HARASSMENT, 1.0, ""))
    assert decision.action is Action.NONE
    assert decision.allowlisted
