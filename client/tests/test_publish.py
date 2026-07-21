from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

import scripticus.publish as publish_module
from scripticus.cli import app
from scripticus.config import Remote, save_remotes
from scripticus.credentials import set_token
from scripticus.pack import pack_package
from scripticus.publish import PublishError, matching_archives, resolve_remote
from scripticus.scaffold import scaffold_package

runner = CliRunner()

URL = "https://scripts.example.com"


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    home_dir = tmp_path / "scripticus-home"
    monkeypatch.setenv("SCRIPTICUS_HOME", str(home_dir))
    monkeypatch.delenv("SCRIPTICUS_TOKEN", raising=False)
    return home_dir


def build_archives(parent: Path, name: str = "my-cool-script") -> list[Path]:
    """Real pack output for a scaffolded package, so matching is exercised
    against the genuine D26 filename scheme.
    """
    source = parent / "src" / name
    scaffold_package("python", name, "acme", source.parent)
    # Python targets every platform, so this packs both format groups.
    return pack_package(source, parent / "builds")


def fake_server(monkeypatch, handler) -> list[httpx.Request]:
    requests: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request)

    transport = httpx.MockTransport(record)
    monkeypatch.setattr(
        publish_module, "_client", lambda: httpx.Client(transport=transport)
    )
    return requests


def success_response(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        201,
        json={
            "namespace": "acme",
            "name": "my-cool-script",
            "version": "0.1.0",
            "content_hash": "abc123",
            "publisher": "kevin",
            "artifacts": [
                {
                    "filename": "my_cool_script-0.1.0-linux.macos-python.tar.gz",
                    "archive_format": "tar.gz",
                    "platforms": ["linux", "macos"],
                    "language": "python",
                    "size": 512,
                },
                {
                    "filename": "my_cool_script-0.1.0-windows-python.zip",
                    "archive_format": "zip",
                    "platforms": ["windows"],
                    "language": "python",
                    "size": 512,
                },
            ],
        },
    )


# --- Archive matching ------------------------------------------------------


def test_matches_every_format_variant_of_the_version(tmp_path):
    archives = build_archives(tmp_path)
    assert len(archives) == 2  # tar.gz + zip

    matched = matching_archives(tmp_path / "builds" / "my-cool-script-0.1.0")
    assert sorted(matched) == sorted(archives)


def test_prefix_matching_is_structural_not_startswith(tmp_path):
    directory = tmp_path / "builds"
    directory.mkdir()
    (directory / "my_tool-0.1.2-linux.macos-bash.tar.gz").write_bytes(b"x")
    (directory / "my_tool-0.1.20-linux.macos-bash.tar.gz").write_bytes(b"x")

    matched = matching_archives(directory / "my-tool-0.1.2")
    assert [p.name for p in matched] == ["my_tool-0.1.2-linux.macos-bash.tar.gz"]


def test_dashed_name_matches_underscore_mangled_filename(tmp_path):
    build_archives(tmp_path)
    # Underscore form in the argument works too — both sides are normalised.
    matched = matching_archives(tmp_path / "builds" / "my_cool_script-0.1.0")
    assert len(matched) == 2


def test_non_archive_files_are_ignored(tmp_path):
    build_archives(tmp_path)
    (tmp_path / "builds" / "my-cool-script-0.1.0.txt").write_text("notes")

    matched = matching_archives(tmp_path / "builds" / "my-cool-script-0.1.0")
    assert all(p.name.endswith((".tar.gz", ".zip")) for p in matched)


def test_no_matching_archives_is_an_error_suggesting_pack(tmp_path):
    build_archives(tmp_path)
    with pytest.raises(PublishError, match="run 'scripticus pack' first"):
        matching_archives(tmp_path / "builds" / "my-cool-script-9.9.9")


def test_missing_directory_is_an_error(tmp_path):
    with pytest.raises(PublishError, match="no such directory"):
        matching_archives(tmp_path / "nowhere" / "my-tool-0.1.0")


# --- Remote resolution ------------------------------------------------------


def test_default_remote_is_the_first_configured():
    remotes = [Remote(name="origin", url="https://a"), Remote(name="public", url="https://b")]
    assert resolve_remote(None, remotes) == remotes[0]
    assert resolve_remote("public", remotes) == remotes[1]


def test_unknown_remote_name_lists_the_configured_ones():
    remotes = [Remote(name="origin", url="https://a")]
    with pytest.raises(PublishError, match=r"no remote named 'nope' \(remotes: origin\)"):
        resolve_remote("nope", remotes)


def test_no_remotes_configured_points_at_login():
    with pytest.raises(PublishError, match="scripticus login"):
        resolve_remote(None, [])


# --- The publish command -----------------------------------------------------


def test_publish_sends_batch_with_stored_token_and_reports_result(
    home, tmp_path, monkeypatch
):
    build_archives(tmp_path)
    save_remotes(home, [Remote(name="origin", url=URL)])
    set_token(home, URL, "tok-123")
    requests = fake_server(monkeypatch, success_response)

    result = runner.invoke(
        app, ["publish", str(tmp_path / "builds" / "my-cool-script-0.1.0")]
    )
    assert result.exit_code == 0, result.output

    (request,) = requests  # the whole batch went as one request (D37)
    assert request.url == URL + "/packages"
    assert request.headers["Authorization"] == "token tok-123"
    body = request.read()
    assert body.count(b'name="archives"') == 2
    assert b"my_cool_script-0.1.0-linux.macos-python.tar.gz" in body
    assert b"my_cool_script-0.1.0-windows-python.zip" in body

    assert "Published my-cool-script 0.1.0:" in result.output
    assert "my_cool_script-0.1.0-linux.macos-python.tar.gz" in result.output
    assert "my_cool_script-0.1.0-windows-python.zip" in result.output


def test_scripticus_token_overrides_the_stored_token(home, tmp_path, monkeypatch):
    build_archives(tmp_path)
    save_remotes(home, [Remote(name="origin", url=URL)])
    set_token(home, URL, "stored")
    monkeypatch.setenv("SCRIPTICUS_TOKEN", "from-ci")
    requests = fake_server(monkeypatch, success_response)

    result = runner.invoke(
        app, ["publish", str(tmp_path / "builds" / "my-cool-script-0.1.0")]
    )
    assert result.exit_code == 0, result.output
    assert requests[0].headers["Authorization"] == "token from-ci"


def test_remote_option_targets_a_non_default_remote(home, tmp_path, monkeypatch):
    build_archives(tmp_path)
    save_remotes(
        home,
        [Remote(name="origin", url=URL), Remote(name="public", url="https://pub.example.org")],
    )
    set_token(home, "https://pub.example.org", "tok-pub")
    requests = fake_server(monkeypatch, success_response)

    result = runner.invoke(
        app,
        ["publish", str(tmp_path / "builds" / "my-cool-script-0.1.0"), "--remote", "public"],
    )
    assert result.exit_code == 0, result.output
    assert requests[0].url == "https://pub.example.org/packages"
    assert requests[0].headers["Authorization"] == "token tok-pub"


def test_not_logged_in_is_an_actionable_error_with_no_request(home, tmp_path, monkeypatch):
    build_archives(tmp_path)
    save_remotes(home, [Remote(name="origin", url=URL)])
    requests = fake_server(monkeypatch, success_response)

    result = runner.invoke(
        app, ["publish", str(tmp_path / "builds" / "my-cool-script-0.1.0")]
    )
    assert result.exit_code == 1
    assert "not logged in to 'origin'" in result.output
    assert "scripticus login origin" in result.output
    assert requests == []


def test_401_maps_to_a_re_login_message(home, tmp_path, monkeypatch):
    build_archives(tmp_path)
    save_remotes(home, [Remote(name="origin", url=URL)])
    set_token(home, URL, "stale")
    fake_server(monkeypatch, lambda request: httpx.Response(401, json={"detail": "bad token"}))

    result = runner.invoke(
        app, ["publish", str(tmp_path / "builds" / "my-cool-script-0.1.0")]
    )
    assert result.exit_code == 1
    assert "'origin' rejected the token" in result.output
    assert "scripticus login origin" in result.output


def test_server_rejection_surfaces_the_detail(home, tmp_path, monkeypatch):
    build_archives(tmp_path)
    save_remotes(home, [Remote(name="origin", url=URL)])
    set_token(home, URL, "tok-123")
    fake_server(
        monkeypatch,
        lambda request: httpx.Response(
            409, json={"detail": "versions are immutable"}
        ),
    )

    result = runner.invoke(
        app, ["publish", str(tmp_path / "builds" / "my-cool-script-0.1.0")]
    )
    assert result.exit_code == 1
    assert "409" in result.output
    assert "versions are immutable" in result.output


def test_unreachable_remote_is_a_clean_error(home, tmp_path, monkeypatch):
    build_archives(tmp_path)
    save_remotes(home, [Remote(name="origin", url=URL)])
    set_token(home, URL, "tok-123")

    def unreachable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    fake_server(monkeypatch, unreachable)

    result = runner.invoke(
        app, ["publish", str(tmp_path / "builds" / "my-cool-script-0.1.0")]
    )
    assert result.exit_code == 1
    assert "cannot reach 'origin'" in result.output
