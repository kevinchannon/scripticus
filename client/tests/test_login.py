from pathlib import Path

import pytest
from typer.testing import CliRunner

from scripticus.cli import app
from scripticus.config import Remote, load_remotes, save_remotes
from scripticus.credentials import load_credentials

runner = CliRunner()

URL = "https://scripts.example.com"


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    home_dir = tmp_path / "scripticus-home"
    monkeypatch.setenv("SCRIPTICUS_HOME", str(home_dir))
    return home_dir


def test_login_to_configured_remote_stores_token(home):
    save_remotes(home, [Remote(name="origin", url=URL)])

    result = runner.invoke(app, ["login", "origin"], input="tok-123\n")
    assert result.exit_code == 0, result.output
    assert "Logged in to origin" in result.output
    assert URL in result.output

    assert load_credentials(home) == {URL: "tok-123"}
    assert load_remotes(home) == [Remote(name="origin", url=URL)]


def test_token_is_not_echoed(home):
    save_remotes(home, [Remote(name="origin", url=URL)])
    result = runner.invoke(app, ["login", "origin"], input="s3cret\n")
    assert result.exit_code == 0, result.output
    assert "s3cret" not in result.output


def test_login_to_unknown_remote_without_url_names_the_two_arg_form(home):
    save_remotes(home, [Remote(name="origin", url=URL)])

    result = runner.invoke(app, ["login", "public"])
    assert result.exit_code == 1
    assert "no remote named 'public'" in result.output
    assert "scripticus login public <url>" in result.output
    assert load_credentials(home) == {}


def test_first_time_login_with_url_registers_remote_at_lowest_priority(home):
    save_remotes(home, [Remote(name="origin", url=URL)])

    result = runner.invoke(
        app, ["login", "public", "https://scripticus.example.org"], input="tok-pub\n"
    )
    assert result.exit_code == 0, result.output

    assert load_remotes(home) == [
        Remote(name="origin", url=URL),
        Remote(name="public", url="https://scripticus.example.org"),
    ]
    assert load_credentials(home) == {"https://scripticus.example.org": "tok-pub"}


def test_first_login_ever_needs_no_existing_config(home):
    result = runner.invoke(app, ["login", "origin", URL], input="tok-123\n")
    assert result.exit_code == 0, result.output
    assert load_remotes(home) == [Remote(name="origin", url=URL)]
    assert load_credentials(home) == {URL: "tok-123"}


def test_matching_url_is_redundant_confirmation_not_a_change(home):
    save_remotes(home, [Remote(name="origin", url=URL)])
    config_before = (home / "config.toml").read_text()

    result = runner.invoke(app, ["login", "origin", URL], input="tok-123\n")
    assert result.exit_code == 0, result.output
    assert (home / "config.toml").read_text() == config_before
    assert load_credentials(home) == {URL: "tok-123"}


def test_conflicting_url_is_refused_with_nothing_written(home):
    save_remotes(home, [Remote(name="origin", url=URL)])
    config_before = (home / "config.toml").read_text()

    result = runner.invoke(
        app, ["login", "origin", "https://elsewhere.example.net"], input="tok-123\n"
    )
    assert result.exit_code == 1
    assert "already configured with a different URL" in result.output
    assert URL in result.output

    assert (home / "config.toml").read_text() == config_before
    assert load_credentials(home) == {}
    assert not (home / "credentials.toml").exists()


def test_relogin_replaces_the_stored_token(home):
    save_remotes(home, [Remote(name="origin", url=URL)])
    runner.invoke(app, ["login", "origin"], input="old-token\n")

    result = runner.invoke(app, ["login", "origin"], input="new-token\n")
    assert result.exit_code == 0, result.output
    assert load_credentials(home) == {URL: "new-token"}
