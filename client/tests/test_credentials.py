import os
import stat
from pathlib import Path

import pytest

from scripticus.config import Remote
from scripticus.credentials import (
    CredentialsError,
    load_credentials,
    resolve_token,
    set_token,
)

URL = "https://scripts.example.com"
REMOTE = Remote(name="origin", url=URL)


@pytest.fixture
def home(tmp_path) -> Path:
    return tmp_path / "scripticus-home"


def test_missing_credentials_file_means_empty_store(home):
    assert load_credentials(home) == {}


def test_set_token_round_trips(home):
    set_token(home, URL, "tok-123")
    assert load_credentials(home) == {URL: "tok-123"}


def test_set_token_replaces_existing_entry_for_same_url(home):
    set_token(home, URL, "old")
    set_token(home, URL, "new")
    assert load_credentials(home) == {URL: "new"}


def test_tokens_for_different_remotes_coexist(home):
    set_token(home, URL, "tok-a")
    set_token(home, "https://other.example.org", "tok-b")
    assert load_credentials(home) == {URL: "tok-a", "https://other.example.org": "tok-b"}


@pytest.mark.skipif(os.name == "nt", reason="no POSIX permissions on Windows")
def test_credentials_file_is_owner_read_write_only(home):
    set_token(home, URL, "tok-123")
    mode = stat.S_IMODE((home / "credentials.toml").stat().st_mode)
    assert mode == 0o600


@pytest.mark.skipif(os.name == "nt", reason="no POSIX permissions on Windows")
def test_rewrite_re_asserts_permissions(home):
    set_token(home, URL, "tok-123")
    os.chmod(home / "credentials.toml", 0o644)
    set_token(home, URL, "tok-456")
    mode = stat.S_IMODE((home / "credentials.toml").stat().st_mode)
    assert mode == 0o600


def test_resolve_token_prefers_environment_over_stored(home):
    set_token(home, URL, "stored")
    token = resolve_token(REMOTE, home, environ={"SCRIPTICUS_TOKEN": "from-ci"})
    assert token == "from-ci"


def test_resolve_token_falls_back_to_stored(home):
    set_token(home, URL, "stored")
    assert resolve_token(REMOTE, home, environ={}) == "stored"


def test_resolve_token_ignores_empty_environment_variable(home):
    set_token(home, URL, "stored")
    assert resolve_token(REMOTE, home, environ={"SCRIPTICUS_TOKEN": ""}) == "stored"


def test_resolve_token_without_any_token_says_how_to_log_in(home):
    with pytest.raises(CredentialsError, match="scripticus login origin"):
        resolve_token(REMOTE, home, environ={})
