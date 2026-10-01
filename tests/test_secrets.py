from datetime import UTC, datetime

import pytest

from negahban.graph import Token
from negahban.secrets import EXPIRES_KEY, FileTokenStore, SecretError, metadata, with_expiry

WHEN = datetime(2026, 11, 30, 12, 0, tzinfo=UTC)


def test_metadata_reads_key_value_lines_and_ignores_the_rest() -> None:
    lines = ["SECRET", "---", "username: me", "a note without a colon", "url: https://x"]
    assert metadata(lines) == {"username": "me", "url": "https://x"}


def test_with_expiry_appends_when_absent_and_keeps_everything_else() -> None:
    lines = ["OLD", "---", "username: me", "remember to rotate"]
    assert with_expiry(lines, "NEW", WHEN) == [
        "NEW",
        "---",
        "username: me",
        "remember to rotate",
        f"{EXPIRES_KEY}: {WHEN.isoformat()}",
    ]


def test_with_expiry_patches_in_place_when_present() -> None:
    lines = ["OLD", f"{EXPIRES_KEY}: 2020-01-01T00:00:00+00:00", "username: me"]
    assert with_expiry(lines, "NEW", WHEN) == [
        "NEW",
        f"{EXPIRES_KEY}: {WHEN.isoformat()}",
        "username: me",
    ]


def test_file_store_round_trips_and_is_owner_only(tmp_path) -> None:  # noqa: ANN001
    store = FileTokenStore(tmp_path / "token.json")
    assert store.load() is None
    store.save(Token("abc", WHEN))
    assert store.load() == Token("abc", WHEN)
    assert (tmp_path / "token.json").stat().st_mode & 0o777 == 0o600


def test_file_store_keeps_unknown_expiry() -> None:
    assert Token("abc").needs_refresh
    assert Token("abc").expires_at is None


def test_malformed_expiry_is_a_secret_error(tmp_path) -> None:  # noqa: ANN001
    path = tmp_path / "token.json"
    path.write_text('{"access_token": "abc", "expires_at": "never"}', encoding="utf-8")
    with pytest.raises(SecretError):
        FileTokenStore(path).load()
