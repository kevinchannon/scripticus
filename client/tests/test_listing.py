"""Registry + installed listing (D49/D50): the dnf-style `list` command —
identity globbing (server-side for available via /packages, client-side for
installed), the installed/available split, dedup and installed-exclusion in
the available section, offline `--installed`, and graceful degradation when
the registry is absent."""

import json

import httpx
import pytest
from typer.testing import CliRunner

import scripticus.search as search
from scripticus.cli import app
from scripticus.config import Remote, save_remotes
from scripticus.listing import build_listing
from scripticus.search import SearchError
from scripticus_common.identity_glob import matches as identity_matches

runner = CliRunner()


@pytest.fixture
def home(tmp_path, monkeypatch):
    home_dir = tmp_path / "scripticus-home"
    home_dir.mkdir(parents=True)
    monkeypatch.setenv("SCRIPTICUS_HOME", str(home_dir))
    return home_dir


def seed_installed(home, *packages):
    """packages: (namespace, name, version) triples."""
    lock = {
        "packages": [
            {"namespace": ns, "name": name, "version": version}
            for ns, name, version in packages
        ]
    }
    (home / "installed.lock").write_text(json.dumps(lock))


def summary(namespace, name, version="1.0.0"):
    return {
        "namespace": namespace,
        "name": name,
        "latest_version": version,
        "description": "",
    }


def fake_catalog(monkeypatch, per_remote):
    """Route ``GET /packages`` by host to ``per_remote`` (host -> handler),
    applying the ``glob`` param to any 200 result the way the real server does
    (via the shared primitive), so the fake behaves like ``/packages``.
    Returns the recorded request list."""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.url.path == "/packages"
        response = per_remote[request.url.host](request)
        if response.status_code == 200:
            glob = request.url.params.get("glob")
            kept = [
                summary
                for summary in response.json()["results"]
                if identity_matches(glob, summary["namespace"], summary["name"])
            ]
            return httpx.Response(200, json={"results": kept})
        return response

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(search, "_client", lambda: httpx.Client(transport=transport))
    return requests


def ok(results):
    return lambda request: httpx.Response(200, json={"results": results})


# --- build_listing ---------------------------------------------------------


def test_installed_only_needs_no_network(home, monkeypatch):
    seed_installed(home, ("acme", "one", "1.0.0"), ("infra", "two", "2.0.0"))

    # No _client patch: any network call would raise, proving we don't make one.
    def boom():
        raise AssertionError("should not touch the network")

    monkeypatch.setattr(search, "_client", boom)
    listing = build_listing(home, [], None, None, "installed")
    assert [(e.identity, e.version) for e in listing.installed] == [
        ("acme/one", "1.0.0"),
        ("infra/two", "2.0.0"),
    ]
    assert listing.available == []


def test_available_excludes_already_installed(home, monkeypatch):
    seed_installed(home, ("acme", "one", "1.0.0"))
    fake_catalog(
        monkeypatch,
        {"r.example": ok([summary("acme", "one"), summary("acme", "two")])},
    )
    listing = build_listing(home, [Remote("r", "https://r.example")], None, None, "all")
    assert [e.identity for e in listing.installed] == ["acme/one"]
    assert [e.identity for e in listing.available] == ["acme/two"]


def test_available_dedups_an_identity_across_remotes(home, monkeypatch):
    fake_catalog(
        monkeypatch,
        {
            "a.example": ok([summary("acme", "dup", "1.0.0")]),
            "b.example": ok([summary("acme", "dup", "2.0.0")]),
        },
    )
    remotes = [Remote("a", "https://a.example"), Remote("b", "https://b.example")]
    listing = build_listing(home, remotes, None, None, "available")
    # First remote (priority order) wins the identity.
    assert [(e.identity, e.remote) for e in listing.available] == [("acme/dup", "a")]


def test_available_glob_is_applied_by_the_server(home, monkeypatch):
    # The server (fake) filters; the client forwards the glob and renders.
    requests = fake_catalog(
        monkeypatch,
        {"r.example": ok([summary("acme", "one"), summary("infra", "two")])},
    )
    listing = build_listing(home, [Remote("r", "https://r.example")], None, "acme/*", "available")
    assert [e.identity for e in listing.available] == ["acme/one"]
    assert requests[0].url.params["glob"] == "acme/*"


def test_absent_glob_is_omitted_from_the_available_query(home, monkeypatch):
    requests = fake_catalog(monkeypatch, {"r.example": ok([summary("acme", "one")])})
    build_listing(home, [Remote("r", "https://r.example")], None, None, "available")
    assert "glob" not in requests[0].url.params


def test_bare_glob_matches_name_across_namespaces(home, monkeypatch):
    seed_installed(home, ("acme", "db-backup", "1.0.0"), ("infra", "logrotate", "2.0.0"))
    listing = build_listing(home, [], None, "db-*", "installed")
    assert [e.identity for e in listing.installed] == ["acme/db-backup"]


def test_unknown_forced_remote_is_an_error(home, monkeypatch):
    with pytest.raises(SearchError, match="no remote named 'nope'"):
        build_listing(home, [Remote("r", "https://r.example")], "nope", None, "all")


def test_all_scope_degrades_when_no_remotes(home, monkeypatch):
    seed_installed(home, ("acme", "one", "1.0.0"))
    listing = build_listing(home, [], None, None, "all")
    assert [e.identity for e in listing.installed] == ["acme/one"]
    assert listing.available == []
    assert listing.warnings and "no remotes" in listing.warnings[0]


def test_available_scope_errors_when_no_remotes(home, monkeypatch):
    with pytest.raises(SearchError, match="no remotes"):
        build_listing(home, [], None, None, "available")


# --- CLI -------------------------------------------------------------------


def test_cli_shows_both_sections(home, monkeypatch):
    save_remotes(home, [Remote("r", "https://r.example")])
    seed_installed(home, ("acme", "installed-one", "1.0.0"))
    fake_catalog(
        monkeypatch,
        {"r.example": ok([summary("acme", "available-one", "3.0.0")])},
    )
    result = runner.invoke(app, ["list"])
    assert result.exit_code == 0
    assert "Installed packages" in result.stdout
    assert "acme/installed-one" in result.stdout
    assert "Available packages" in result.stdout
    assert "acme/available-one" in result.stdout


def test_cli_installed_flag_is_offline(home, monkeypatch):
    save_remotes(home, [Remote("r", "https://r.example")])
    seed_installed(home, ("acme", "one", "1.0.0"))

    def boom():
        raise AssertionError("should not touch the network")

    monkeypatch.setattr(search, "_client", boom)
    result = runner.invoke(app, ["list", "--installed"])
    assert result.exit_code == 0
    assert "Installed packages" in result.stdout
    assert "Available packages" not in result.stdout


def test_cli_mutually_exclusive_flags(home, monkeypatch):
    result = runner.invoke(app, ["list", "--installed", "--available"])
    assert result.exit_code == 1
    assert "mutually exclusive" in result.stdout


def test_cli_no_matches_reports_cleanly(home, monkeypatch):
    save_remotes(home, [Remote("r", "https://r.example")])
    fake_catalog(monkeypatch, {"r.example": ok([])})
    result = runner.invoke(app, ["list", "nothing-matches-*"])
    assert result.exit_code == 0
    assert "No packages found." in result.stdout


def test_cli_glob_filters(home, monkeypatch):
    save_remotes(home, [Remote("r", "https://r.example")])
    seed_installed(home, ("acme", "keep", "1.0.0"), ("infra", "drop", "2.0.0"))
    fake_catalog(monkeypatch, {"r.example": ok([])})
    result = runner.invoke(app, ["list", "acme/*"])
    assert result.exit_code == 0
    assert "acme/keep" in result.stdout
    assert "infra/drop" not in result.stdout
