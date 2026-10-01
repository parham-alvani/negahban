"""Turn verdicts into actions.

The policy is deliberately cautious: hiding is reversible and is the default
response to abuse; deleting is reserved for spam/scam and only when asked for;
civil negativity is never touched, only flagged. Anything the model is unsure
about is flagged for a human rather than acted on.
"""

from __future__ import annotations

from dataclasses import dataclass

from negahban.models import Action, Comment, Decision, Label, Verdict

_ABUSIVE = frozenset({Label.INSULT, Label.HARASSMENT, Label.HATE, Label.SEXUAL})
_JUNK = frozenset({Label.SPAM, Label.SCAM})


@dataclass(frozen=True, slots=True)
class Policy:
    """Thresholds and switches that decide what happens to a labelled comment."""

    hide_threshold: float = 0.8
    """Minimum confidence before an abusive or junk comment is hidden."""

    delete_junk: bool = False
    """Delete (instead of hide) spam/scam above ``hide_threshold``."""

    allowlist: frozenset[str] = frozenset()
    """Lowercased usernames whose comments are never acted on."""

    def decide(self, comment: Comment, verdict: Verdict) -> Decision:
        if comment.username.lower() in self.allowlist:
            return Decision(comment, verdict, Action.NONE, allowlisted=True)

        label = verdict.label
        if label is Label.OK:
            return Decision(comment, verdict, Action.NONE)
        if label is Label.NEGATIVE_LEGIT:
            return Decision(comment, verdict, Action.FLAG)

        # Abusive or junk, but not confidently so: a human should look.
        if verdict.confidence < self.hide_threshold:
            return Decision(comment, verdict, Action.FLAG)

        if label in _JUNK and self.delete_junk:
            return Decision(comment, verdict, Action.DELETE)
        if label in _JUNK or label in _ABUSIVE:
            return Decision(comment, verdict, Action.HIDE)
        return Decision(comment, verdict, Action.FLAG)
