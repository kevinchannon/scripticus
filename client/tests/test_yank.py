from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

import scripticus.yank as yank_module
from scripticus.cli import app
from scripticus.config import Remote, save_remotes
from scripticus.credentials import set_token
from scripticus.yank import YankError, parse_target, resolve_remote

runner = CliRunner()

URL = "https://scripts.example.com"


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    home_dir = tmp_path / "scripticus-home"
    monkeypatch.setenv("SCRIPTICUS_HOME", str(home_dir))
    monkeypatch.delenv("SCRIPTICUS_TOKEN", raising=False)
    return home_dir


def fake_server(monkeypatch, handler) -> list[httpx.Request]:
    requests: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request)

    transport = httpx.MockTransport(record)
    monkeypatch.setattr(
        yank_module, "_client", lambda: httpx.Client(transport=transport)
    )
    return requests


def result_response(yanked: bool):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "namespace": "infra",
                "name": "backup-rotate",
                "version": "1.2.0",
                "yanked": yanked,
            },
        )

    return handler


# --- Target parsing ---------------------------------------------------------


def test_parse_target_splits_namespace_name_version():
    assert parse_target("infra/backup-rotate@1.2.0") == (
        "infra",
        "backup-rotate",
        "1.2.0",
    )


def test_parse_target_requires_a_namespace():
    with pytest.raises(YankError, match="fully-namespaced"):
        parse_target("backup-rotate@1.2.0")


def test_parse_target_requires_a_version():
    with pytest.raises(YankError, match="missing a version"):
        parse_target("infra/backup-rotate")


def test_parse_target_rejects_a_range_not_an_exact_version():
    with pytest.raises(YankError, match="not an exact version"):
        parse_target("infra/backup-rotate@^1.2.0")


def test_parse_target_rejects_a_partial_version():
    with pytest.raises(YankError, match="not an exact version"):
        parse_target("infra/backup-rotate@1.2")


# --- Remote resolution ------------------------------------------------------


def test_default_remote_is_the_first_configured():
    remotes = [Remote(name="origin", url="https://a"), Remote(name="public", url="https://b")]
    assert resolve_remote(None, remotes) == remotes[0]
    assert resolve_remote("public", remotes) == remotes[1]


def test_no_remotes_configured_points_at_login():
    with pytest.raises(YankError, match="scripticus login"):
        resolve_remote(None, [])


# --- The yank command -------------------------------------------------------


def test_yank_sends_patch_with_stored_token_and_reports_result(home, monkeypatch):
    save_remotes(home, [Remote(name="origin", url=URL)])
    set_token(home, URL, "tok-123")
    requests = fake_server(monkeypatch, result_response(True))

    result = runner.invoke(app, ["yank", "infra/backup-rotate@1.2.0"])
    assert result.exit_code == 0, result.output

    (request,) = requests
    assert request.method == "PATCH"
    assert request.url == URL + "/packages/infra/backup-rotate/1.2.0"
    assert request.headers["Authorization"] == "token tok-123"
    assert request.read() == b'{"yanked":true}'
    assert "Yanked infra/backup-rotate@1.2.0 on 'origin'" in result.output


def test_undo_sends_yanked_false_and_reports_un_yank(home, monkeypatch):
    save_remotes(home, [Remote(name="origin", url=URL)])
    set_token(home, URL, "tok-123")
    requests = fake_server(monkeypatch, result_response(False))

    result = runner.invoke(app, ["yank", "--undo", "infra/backup-rotate@1.2.0"])
    assert result.exit_code == 0, result.output
    assert requests[0].read() == b'{"yanked":false}'
    assert "Un-yanked infra/backup-rotate@1.2.0 on 'origin'" in result.output


def test_scripticus_token_overrides_the_stored_token(home, monkeypatch):
    save_remotes(home, [Remote(name="origin", url=URL)])
    set_token(home, URL, "stored")
    monkeypatch.setenv("SCRIPTICUS_TOKEN", "from-ci")
    requests = fake_server(monkeypatch, result_response(True))

    result = runner.invoke(app, ["yank", "infra/backup-rotate@1.2.0"])
    assert result.exit_code == 0, result.output
    assert requests[0].headers["Authorization"] == "token from-ci"


def test_remote_option_targets_a_non_default_remote(home, monkeypatch):
    save_remotes(
        home,
        [Remote(name="origin", url=URL), Remote(name="public", url="https://pub.example.org")],
    )
    set_token(home, "https://pub.example.org", "tok-pub")
    requests = fake_server(monkeypatch, result_response(True))

    result = runner.invoke(
        app, ["yank", "infra/backup-rotate@1.2.0", "--remote", "public"]
    )
    assert result.exit_code == 0, result.output
    assert requests[0].url == "https://pub.example.org/packages/infra/backup-rotate/1.2.0"
    assert requests[0].headers["Authorization"] == "token tok-pub"


def test_invalid_target_fails_before_any_request(home, monkeypatch):
    save_remotes(home, [Remote(name="origin", url=URL)])
    set_token(home, URL, "tok-123")
    requests = fake_server(monkeypatch, result_response(True))

    result = runner.invoke(app, ["yank", "infra/backup-rotate@^1.2.0"])
    assert result.exit_code == 1
    assert "not an exact version" in result.output
    assert requests == []


def test_not_logged_in_is_an_actionable_error_with_no_request(home, monkeypatch):
    save_remotes(home, [Remote(name="origin", url=URL)])
    requests = fake_server(monkeypatch, result_response(True))

    result = runner.invoke(app, ["yank", "infra/backup-rotate@1.2.0"])
    assert result.exit_code == 1
    assert "scripticus login origin" in result.output
    assert requests == []


def test_401_maps_to_a_re_login_message(home, monkeypatch):
    save_remotes(home, [Remote(name="origin", url=URL)])
    set_token(home, URL, "stale")
    fake_server(monkeypatch, lambda request: httpx.Response(401, json={"detail": "bad token"}))

    result = runner.invoke(app, ["yank", "infra/backup-rotate@1.2.0"])
    assert result.exit_code == 1
    assert "rejected the token" in result.output
    assert "scripticus login origin" in result.output


def test_404_surfaces_the_server_detail(home, monkeypatch):
    save_remotes(home, [Remote(name="origin", url=URL)])
    set_token(home, URL, "tok-123")
    fake_server(
        monkeypatch,
        lambda request: httpx.Response(404, json={"detail": "no version 1.2.0 of 'infra/backup-rotate'"}),
    )

    result = runner.invoke(app, ["yank", "infra/backup-rotate@1.2.0"])
    assert result.exit_code == 1
    assert "failed (404)" in result.output
    assert "no version 1.2.0" in result.output
