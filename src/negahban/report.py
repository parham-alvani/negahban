"""Rich terminal output."""

from __future__ import annotations

from collections.abc import Sequence

from rich.console import Console
from rich.table import Table

from negahban.audit import AuditRow
from negahban.models import Action, Decision

_ACTION_STYLE = {
    Action.NONE: "dim",
    Action.FLAG: "yellow",
    Action.HIDE: "red",
    Action.DELETE: "bold red",
}

_TEXT_WIDTH = 60


def _clip(text: str, width: int = _TEXT_WIDTH) -> str:
    text = " ".join(text.split())
    return text if len(text) <= width else text[: width - 1] + "…"


def print_decisions(decisions: Sequence[Decision], *, console: Console) -> None:
    """One row per comment that is not plainly ok; then a count summary."""
    table = Table(title="Decisions", show_lines=False)
    table.add_column("action", no_wrap=True)
    table.add_column("label", no_wrap=True)
    table.add_column("conf", justify="right", no_wrap=True)
    table.add_column("user", no_wrap=True)
    table.add_column("comment")
    table.add_column("reason", style="dim")

    for decision in decisions:
        if decision.action is Action.NONE and not decision.allowlisted:
            continue
        action = decision.action.value + (" (allowlisted)" if decision.allowlisted else "")
        table.add_row(
            f"[{_ACTION_STYLE[decision.action]}]{action}[/]",
            decision.verdict.label.value,
            f"{decision.verdict.confidence:.2f}",
            f"@{decision.comment.username}",
            _clip(decision.comment.text),
            _clip(decision.verdict.reason, 50),
        )

    if table.row_count:
        console.print(table)

    counts = {action: 0 for action in Action}
    for decision in decisions:
        counts[decision.action] += 1
    console.print(
        f"\n{len(decisions)} judged: "
        f"[dim]{counts[Action.NONE]} ok[/], "
        f"[yellow]{counts[Action.FLAG]} flagged[/], "
        f"[red]{counts[Action.HIDE]} to hide[/], "
        f"[bold red]{counts[Action.DELETE]} to delete[/]."
    )


def print_rows(rows: Sequence[AuditRow], *, console: Console, title: str) -> None:
    table = Table(title=title)
    table.add_column("comment_id", no_wrap=True)
    table.add_column("action", no_wrap=True)
    table.add_column("applied", no_wrap=True)
    table.add_column("label", no_wrap=True)
    table.add_column("user", no_wrap=True)
    table.add_column("comment")
    table.add_column("judged", style="dim", no_wrap=True)
    for row in rows:
        style = _ACTION_STYLE.get(Action(row.action), "")
        table.add_row(
            row.comment_id,
            f"[{style}]{row.action}[/]",
            "yes" if row.applied else "no",
            row.label,
            f"@{row.username}",
            _clip(row.text),
            row.judged_at[:16],
        )
    console.print(table)
