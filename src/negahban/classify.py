"""Comment classification with Claude.

Comments are judged in batches (one request per post) with the post's caption
as context, and the answer is a structured JSON document validated by
pydantic — no free-text parsing. Instagram's own "Hidden Words" filter already
handles keyword lists; the point of using a model is everything a list can't
do: sarcasm, slang and transliterated Persian (Finglish), and telling a real
complaint apart from an insult.
"""

from __future__ import annotations

from collections.abc import Sequence

import anthropic
from pydantic import BaseModel, Field

from negahban.models import Comment, Label, Media, Verdict

# Posts with hundreds of comments are split into requests of this size so a
# single response stays short and any one failure costs little.
BATCH_SIZE = 25

SYSTEM_PROMPT = """\
You moderate the comments on a personal Instagram account. The comments may be in
Persian (Farsi), transliterated Persian written with Latin letters (Finglish), or
English, and are often informal.

For each comment decide one label:

- ok: harmless. Includes jokes, teasing between friends, emoji-only, and praise.
- negative_legit: critical or unhappy, but civil. A genuine complaint, a
  disagreement, constructive criticism, or disappointment. This is NOT abuse.
- insult: name-calling or demeaning language aimed at the account owner or
  another commenter.
- harassment: threats, intimidation, persistent targeting, or doxxing.
- hate: attacks on a group's ethnicity, religion, nationality, gender, or
  sexual orientation.
- sexual: unsolicited sexual remarks or solicitation.
- spam: unsolicited promotion, follow-for-follow, engagement bait, or
  repetitive off-topic content.
- scam: phishing, fake giveaways, crypto/forex schemes, "DM me to earn", or
  impersonation.

Use the post caption only as context for what the comment is reacting to. Be
conservative: when a comment is merely blunt or sarcastic rather than abusive,
prefer negative_legit or ok. Replies may be addressed to another commenter, not
the account owner.

Return one verdict per comment, keyed by the given comment id. "confidence" is
your probability (0 to 1) that the label is right. "reason" is one short
sentence, in English.
"""


class _VerdictOut(BaseModel):
    comment_id: str
    label: Label
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str


class _BatchOut(BaseModel):
    verdicts: list[_VerdictOut]


def _render_batch(media: Media, comments: Sequence[Comment]) -> str:
    lines = [f"Post caption:\n{media.caption or '(no caption)'}\n", "Comments:"]
    for comment in comments:
        kind = "reply" if comment.is_reply else "comment"
        author = comment.username or "(unknown)"
        lines.append(f"- id={comment.comment_id} {kind} by @{author}: {comment.text}")
    return "\n".join(lines)


class Classifier:
    """Judge comments with Claude, one request per batch."""

    def __init__(
        self,
        model: str,
        client: anthropic.Anthropic | None = None,
        api_key: str | None = None,
    ) -> None:
        self._model = model
        # With no key given the SDK resolves ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN
        # on its own; a key from gopass is passed explicitly.
        self._client = client or anthropic.Anthropic(api_key=api_key)

    def classify(self, media: Media, comments: Sequence[Comment]) -> dict[str, Verdict]:
        """Return ``{comment_id: Verdict}`` for every comment given.

        A comment the model fails to answer for is simply absent from the
        result, so callers must treat a missing key as "not judged".
        """
        verdicts: dict[str, Verdict] = {}
        for start in range(0, len(comments), BATCH_SIZE):
            batch = comments[start : start + BATCH_SIZE]
            verdicts.update(self._classify_batch(media, batch))
        return verdicts

    def _classify_batch(self, media: Media, batch: Sequence[Comment]) -> dict[str, Verdict]:
        wanted = {comment.comment_id for comment in batch}
        response = self._client.messages.parse(
            model=self._model,
            max_tokens=8000,
            system=SYSTEM_PROMPT,
            # Classification is routine work; low effort keeps it cheap and fast.
            output_config={"effort": "low"},
            messages=[{"role": "user", "content": _render_batch(media, batch)}],
            output_format=_BatchOut,
        )
        parsed = response.parsed_output
        if parsed is None:
            return {}
        return {
            item.comment_id: Verdict(
                label=item.label, confidence=item.confidence, reason=item.reason
            )
            for item in parsed.verdicts
            if item.comment_id in wanted
        }
