from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

import scripticus.whoami as whoami_module
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


def whoami_server(monkeypatch, handler) -> list[httpx.Request]:
    """Point the login command's whoami call at an in-memory server. Returns
    the list of requests it received, so a test can assert on the header/URL.
    """
    requests: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request)

    transport = httpx.MockTransport(record)
    monkeypatch.setattr(
        whoami_module, "_client", lambda: httpx.Client(transport=transport)
    )
    return requests


def ok_whoami(username: str = "kevin-c"):
    return lambda request: httpx.Response(200, json={"username": username})


@pytest.fixture(autouse=True)
def default_whoami(monkeypatch) -> None:
    """Every login verifies the token first (D41); by default that succeeds.
    Tests exercising a bad token or an unreachable remote re-patch the seam
    (last patch wins).
    """
    whoami_server(monkeypatch, ok_whoami())


def test_login_to_configured_remote_stores_token(home):
    save_remotes(home, [Remote(name="origin", url=URL)])

    result = runner.invoke(app, ["login", "origin"], input="tok-123\n")
    assert result.exit_code == 0, result.output
    assert "Logged in to origin" in result.output
    assert URL in result.output
    assert "as kevin-c" in result.output

    assert load_credentials(home) == {URL: "tok-123"}
    assert load_remotes(home) == [Remote(name="origin", url=URL)]


def test_login_verifies_token_before_storing(home, monkeypatch):
    save_remotes(home, [Remote(name="origin", url=URL)])
    requests = whoami_server(monkeypatch, ok_whoami(username="alice"))

    result = runner.invoke(app, ["login", "origin"], input="tok-123\n")
    assert result.exit_code == 0, result.output
    assert "as alice" in result.output

    (request,) = requests
    assert request.url == URL + "/whoami"
    assert request.headers["Authorization"] == "token tok-123"


def test_token_is_not_echoed(home):
    save_remotes(home, [Remote(name="origin", url=URL)])
    result = runner.invoke(app, ["login", "origin"], input="s3cret\n")
    assert result.exit_code == 0, result.output
    assert "s3cret" not in result.output


def test_bad_token_is_rejected_and_nothing_stored(home, monkeypatch):
    save_remotes(home, [Remote(name="origin", url=URL)])
    whoami_server(
        monkeypatch, lambda request: httpx.Response(401, json={"detail": "bad token"})
    )

    result = runner.invoke(app, ["login", "origin"], input="wrong-token\n")
    assert result.exit_code == 1
    assert "rejected the token" in result.output

    assert load_credentials(home) == {}
    assert not (home / "credentials.toml").exists()


def test_unreachable_remote_refuses_and_distinguishes_from_a_bad_token(home, monkeypatch):
    save_remotes(home, [Remote(name="origin", url=URL)])

    def unreachable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    whoami_server(monkeypatch, unreachable)

    result = runner.invoke(app, ["login", "origin"], input="tok-123\n")
    assert result.exit_code == 1
    assert "could not reach 'origin'" in result.output
    # The message must not blame the token — the remote may simply be down.
    assert "rejected" not in result.output
    assert load_credentials(home) == {}


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


def test_first_time_registration_persists_even_when_the_token_is_bad(home, monkeypatch):
    # Config is written before the token prompt (to fail early on an
    # unwritable config), so a rejected token leaves the remote registered
    # but stores no credential — a plain re-login recovers.
    whoami_server(
        monkeypatch, lambda request: httpx.Response(401, json={"detail": "bad token"})
    )

    result = runner.invoke(app, ["login", "public", URL], input="wrong\n")
    assert result.exit_code == 1
    assert load_remotes(home) == [Remote(name="public", url=URL)]
    assert load_credentials(home) == {}


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
