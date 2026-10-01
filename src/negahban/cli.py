"""Command-line entry point."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from negahban import graph, report
from negahban.audit import AuditLog
from negahban.classify import Classifier
from negahban.config import (
    AUDIT_DB,
    DEFAULT_MAX_POSTS,
    TOKEN_FILE,
    ConfigError,
    Settings,
    load_settings,
)
from negahban.graph import GraphError, InstagramClient, Token
from negahban.models import Action, Decision
from negahban.policy import Policy
from negahban.secrets import SecretError

app = typer.Typer(
    help="Keep watch over the comments on your Instagram posts.",
    add_completion=False,
)
console = Console()


@app.callback()
def _main() -> None:
    """Keep watch over the comments on your Instagram posts."""


def _fail(message: str) -> typer.Exit:
    console.print(f"[red]{message}[/]")
    return typer.Exit(code=1)


def _settings(token_file: Path) -> Settings:
    try:
        return load_settings(token_file)
    except ConfigError as error:
        raise _fail(str(error)) from error


def _load_token(settings: Settings) -> Token:
    """Return a valid token from the store, refreshing (and re-saving) near expiry."""
    store = settings.token_store
    token = store.load()
    if token is None:
        raise _fail(f"No token in {store.describe()}. Run `negahban auth` first.")
    if token.needs_refresh:
        console.print("[cyan]Access token is near expiry — refreshing...[/]")
        try:
            token = graph.refresh_long_lived(token)
        except GraphError as error:
            raise _fail(f"Could not refresh token: {error}") from error
        _save_token(settings, token)
    return token


def _save_token(settings: Settings, token: Token) -> None:
    try:
        settings.token_store.save(token)
    except SecretError as error:
        raise _fail(f"Could not save token: {error}") from error


@app.command()
def auth(
    token: Annotated[
        str | None,
        typer.Option(
            "--token",
            help="An access token to store. Without it, the token already in the store "
            "(e.g. pasted into the gopass entry) is used.",
        ),
    ] = None,
    token_file: Annotated[
        Path,
        typer.Option("--token-file", help="Token file, used when NEGAHBAN_GOPASS_TOKEN is unset."),
    ] = TOKEN_FILE,
) -> None:
    """Exchange and store an Instagram access token; report which account it is for.

    Get a token from the app's use case page on developers.facebook.com
    ("Generate access tokens", after adding the account as an Instagram Tester).
    """
    settings = _settings(token_file)
    store = settings.token_store

    raw = token
    if raw is None:
        existing = store.load()
        if existing is None:
            raise _fail(f"No token in {store.describe()}; pass --token or put one there.")
        raw = existing.access_token

    # A short-lived token (from an OAuth code) must be exchanged; a long-lived
    # one (the dashboard's token generator) is rejected by the exchange and
    # must be refreshed instead. Either way we end up with a 60-day token
    # whose expiry we know.
    try:
        stored = graph.exchange_for_long_lived(settings.ig_app_secret, raw)
    except GraphError as exchange_error:
        try:
            stored = graph.refresh_long_lived(raw)
        except GraphError as refresh_error:
            raise _fail(
                f"Token is neither exchangeable ({exchange_error}) "
                f"nor refreshable ({refresh_error})."
            ) from refresh_error
    _save_token(settings, stored)

    try:
        with InstagramClient(stored) as client:
            me = client.me()
    except GraphError as error:
        raise _fail(f"Token works for exchange but /me failed: {error}") from error

    console.print(
        f"[green]Stored token for @{me.get('username')} ({me.get('account_type')}) "
        f"in {store.describe()}; valid until {stored.expires_at:%Y-%m-%d}.[/]"
    )


@app.command()
def scan(
    max_posts: Annotated[
        int,
        typer.Option("--max-posts", help="Number of recent posts to inspect (0 = all)."),
    ] = DEFAULT_MAX_POSTS,
    apply: Annotated[
        bool,
        typer.Option("--apply", help="Actually hide/delete. Without it, scan is a dry run."),
    ] = False,
    hide_threshold: Annotated[
        float,
        typer.Option("--hide-threshold", help="Minimum confidence before hiding."),
    ] = 0.8,
    delete_junk: Annotated[
        bool,
        typer.Option("--delete-junk", help="Delete spam/scam instead of hiding it."),
    ] = False,
    rescan: Annotated[
        bool,
        typer.Option("--rescan", help="Re-judge comments already in the audit log."),
    ] = False,
    token_file: Annotated[Path, typer.Option("--token-file")] = TOKEN_FILE,
    db: Annotated[Path, typer.Option("--db", help="Audit log location.")] = AUDIT_DB,
) -> None:
    """Fetch new comments, classify them, decide, and (with --apply) act."""
    settings = _settings(token_file)
    token = _load_token(settings)
    policy = Policy(
        hide_threshold=hide_threshold, delete_junk=delete_junk, allowlist=settings.allowlist
    )
    classifier = Classifier(settings.model)
    decisions: list[Decision] = []

    with InstagramClient(token) as client, AuditLog(db) as log:
        seen = frozenset() if rescan else log.seen()
        scope = "all posts" if max_posts == 0 else f"the {max_posts} most recent posts"
        console.print(f"[cyan]Fetching comments on {scope}...[/]")
        try:
            medias = client.recent_media(max_posts)
        except GraphError as error:
            raise _fail(str(error)) from error

        with console.status("Classifying...") as status:
            for index, media in enumerate(medias, start=1):
                status.update(f"Classifying... post {index}/{len(medias)}")
                try:
                    comments = [
                        c
                        for c in client.comments(media)
                        if c.comment_id not in seen and not c.hidden
                    ]
                except GraphError as error:
                    console.print(f"[yellow]Skipping {media.permalink}: {error}[/]")
                    continue
                if not comments:
                    continue
                verdicts = classifier.classify(media, comments)
                for comment in comments:
                    verdict = verdicts.get(comment.comment_id)
                    if verdict is None:
                        continue
                    decision = policy.decide(comment, verdict)
                    log.record(decision)
                    decisions.append(decision)

        report.print_decisions(decisions, console=console)

        actionable = [d for d in decisions if d.action in (Action.HIDE, Action.DELETE)]
        if not actionable:
            return
        if not apply:
            console.print("\n[dim]Dry run — nothing changed. Re-run with --apply to act.[/]")
            return

        console.print("")
        for decision in actionable:
            cid = decision.comment.comment_id
            try:
                if decision.action is Action.DELETE:
                    client.delete(cid)
                else:
                    client.hide(cid)
            except GraphError as error:
                console.print(f"[yellow]Could not {decision.action.value} {cid}: {error}[/]")
                continue
            log.mark_applied(cid, decision.action)
            console.print(
                f"[green]{decision.action.value}[/] @{decision.comment.username}: "
                f"{report._clip(decision.comment.text)}"
            )


@app.command()
def unhide(
    comment_id: Annotated[str, typer.Argument(help="Comment id, as shown by `negahban log`.")],
    token_file: Annotated[Path, typer.Option("--token-file")] = TOKEN_FILE,
    db: Annotated[Path, typer.Option("--db")] = AUDIT_DB,
) -> None:
    """Reverse a hide: make the comment visible again and note it in the log."""
    token = _load_token(_settings(token_file))
    with InstagramClient(token) as client, AuditLog(db) as log:
        try:
            client.unhide(comment_id)
        except GraphError as error:
            raise _fail(str(error)) from error
        if log.get(comment_id) is not None:
            log.mark_applied(comment_id, Action.NONE)
    console.print(f"[green]Unhid {comment_id}.[/]")


@app.command()
def log(
    limit: Annotated[int, typer.Option("--limit", help="How many rows to show.")] = 30,
    pending: Annotated[
        bool, typer.Option("--pending", help="Only hides/deletes not yet applied.")
    ] = False,
    db: Annotated[Path, typer.Option("--db")] = AUDIT_DB,
) -> None:
    """Show recent judgements from the audit log."""
    with AuditLog(db) as audit:
        rows = audit.pending() if pending else audit.recent(limit)
    if not rows:
        console.print("[dim]Nothing in the log yet.[/]")
        return
    report.print_rows(rows, console=console, title="Pending actions" if pending else "Audit log")


if __name__ == "__main__":
    app()
