"""Command-line entry point."""

from __future__ import annotations

from datetime import UTC, datetime
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


def _stored_token(settings: Settings) -> Token:
    store = settings.token_store
    try:
        token = store.load()
    except SecretError as error:
        raise _fail(str(error)) from error
    if token is None:
        raise _fail(f"No token in {store.describe()}. Run `negahban auth` first.")
    return token


def _load_token(settings: Settings) -> Token:
    """Return a usable token from the store, refreshing (and re-saving) near expiry.

    A refresh that fails is not fatal while the token itself still works: the
    expiry may simply be unknown (pasted by hand) or still days away.
    """
    token = _stored_token(settings)
    if not token.needs_refresh:
        return token
    console.print("[cyan]Refreshing the access token...[/]")
    try:
        token = graph.refresh_long_lived(token)
    except GraphError as error:
        if token.expires_at is not None and token.expires_at <= datetime.now(UTC):
            raise _fail(f"Token has expired and could not be refreshed: {error}") from error
        console.print(f"[yellow]Could not refresh ({error}); using the stored token as is.[/]")
        return token
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
    given = Token(token) if token is not None else _stored_token(settings)
    _store_long_lived(settings, given)


def _app_secret(settings: Settings) -> str:
    try:
        return settings.app_secret()
    except ConfigError as error:
        raise _fail(str(error)) from error


def _store_long_lived(settings: Settings, given: Token) -> None:
    """Turn any token into a verified 60-day one and persist it."""
    app_secret = _app_secret(settings)

    # A short-lived token (from an OAuth code) must be exchanged; a long-lived
    # one (the dashboard's token generator) is rejected by the exchange and
    # must be refreshed instead. Either way we end up with a 60-day token
    # whose expiry we know.
    try:
        fresh = graph.exchange_for_long_lived(app_secret, given.access_token)
        how = "exchanged"
    except GraphError as exchange_error:
        try:
            fresh = graph.refresh_long_lived(given)
            how = "refreshed"
        except GraphError as refresh_error:
            raise _fail(
                f"Token is neither exchangeable ({exchange_error}) "
                f"nor refreshable ({refresh_error})."
            ) from refresh_error

    # Verify before persisting, so a token with the wrong scopes never
    # replaces a working one in the store.
    try:
        with InstagramClient(fresh) as client:
            me = client.me()
    except GraphError as error:
        raise _fail(f"Token was {how} but /me rejected it, not storing: {error}") from error
    _save_token(settings, fresh)

    expires = f"{fresh.expires_at:%Y-%m-%d}" if fresh.expires_at else "unknown"
    console.print(
        f"[green]Stored {how} token for @{me.get('username')} ({me.get('account_type')}) "
        f"in {settings.token_store.describe()}; valid until {expires}.[/]"
    )


@app.command()
def login(
    token_file: Annotated[
        Path,
        typer.Option("--token-file", help="Token file, used when NEGAHBAN_GOPASS_TOKEN is unset."),
    ] = TOKEN_FILE,
    open_browser: Annotated[
        bool,
        typer.Option("--open/--no-open", help="Open the Instagram login page in a browser."),
    ] = True,
) -> None:
    """Connect an Instagram professional account through Instagram's login page.

    Instagram sends the browser to negahban's callback page, which shows an
    authorization code; paste it here. The code becomes a 60-day token that
    is verified and stored like `negahban auth` does.
    """
    settings = _settings(token_file)
    url = graph.authorize_url(settings.ig_app_id, settings.redirect_uri)

    console.print("Log in with the Instagram professional account negahban should watch:")
    console.print(f"  [link={url}]{url}[/link]\n")
    if open_browser:
        typer.launch(url)

    code = typer.prompt("Paste the code shown on the callback page").strip()
    if not code:
        raise _fail("No code given.")

    try:
        short_lived = graph.exchange_code(
            settings.ig_app_id, _app_secret(settings), settings.redirect_uri, code
        )
    except GraphError as error:
        raise _fail(f"Could not exchange the code: {error}") from error
    _store_long_lived(settings, Token(short_lived))


@app.command()
def hide(
    comment_id: Annotated[str, typer.Argument(help="Comment id to hide.")],
    token_file: Annotated[Path, typer.Option("--token-file")] = TOKEN_FILE,
    db: Annotated[Path, typer.Option("--db")] = AUDIT_DB,
) -> None:
    """Hide one comment by id, bypassing the classifier; recorded in the log."""
    token = _load_token(_settings(token_file))
    with InstagramClient(token) as client, AuditLog(db) as log:
        try:
            client.hide(comment_id)
        except GraphError as error:
            raise _fail(str(error)) from error
        log.record_manual(comment_id, Action.HIDE)
    console.print(f"[green]Hid {comment_id}.[/]")


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
    try:
        classifier = Classifier(settings.model, api_key=settings.anthropic_api_key())
    except ConfigError as error:
        raise _fail(str(error)) from error
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

        # Act on everything decided but not yet done: this run's hides/deletes
        # plus any left over from earlier dry runs, which the skip-already-judged
        # rule above would otherwise never reach again.
        pending = log.pending()
        if not pending:
            return
        if not apply:
            console.print(
                f"\n[dim]Dry run — nothing changed. {len(pending)} action(s) pending; "
                "re-run with --apply to act.[/]"
            )
            return

        console.print("")
        for row in pending:
            action = Action(row.action)
            try:
                if action is Action.DELETE:
                    client.delete(row.comment_id)
                else:
                    client.hide(row.comment_id)
            except GraphError as error:
                console.print(f"[yellow]Could not {action.value} {row.comment_id}: {error}[/]")
                continue
            log.mark_applied(row.comment_id, action)
            console.print(f"[green]{action.value}[/] @{row.username}: {report._clip(row.text)}")


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
        log.record_manual(comment_id, Action.NONE)
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
