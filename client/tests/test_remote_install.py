"""Remote install (D42/D46): target parsing, remote selection, planning, and
the full resolve -> download -> verify -> install CLI flow against a fake
server (httpx.MockTransport) with genuinely packed archives, so tree-hash
verification is exercised for real."""

import json
import os
import subprocess
import tempfile
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

import scripticus.remote_install as remote_install
import scripticus.tools as tools_module
from scripticus.cli import app
from scripticus.config import Remote, save_remotes
from scripticus.credentials import set_token
from scripticus.pack import pack_package
from scripticus.remote_install import (
    RemoteInstallError,
    ResolveError,
    installed_closure,
    parse_target,
    resolve_root,
)
from scripticus.scaffold import scaffold_package
from scripticus_schema.resolve_api import ResolveRequest
from scripticus_schema.treehash import tree_hash

runner = CliRunner()
URL = "https://reg.example.com"


@pytest.fixture
def home(tmp_path, monkeypatch):
    home_dir = tmp_path / "scripticus-home"
    monkeypatch.setenv("SCRIPTICUS_HOME", str(home_dir))
    monkeypatch.delenv("SCRIPTICUS_TOKEN", raising=False)
    return home_dir


def make_package(
    tmp_path, name, namespace="acme", version="0.1.0", language="python", extra_toml=""
):
    """Scaffold + pack a real package; return (tar.gz archive path, content hash,
    download pointer). The hash is the tree hash of the packed tree (D27), which
    is exactly what the client recomputes after downloading."""
    src_parent = Path(tempfile.mkdtemp(dir=tmp_path))
    scaffold_package(language, name, namespace, src_parent)
    pkg_dir = src_parent / name
    manifest = pkg_dir / "meta.toml"
    manifest.write_text(
        manifest.read_text().replace('version = "0.1.0"', f'version = "{version}"')
        + extra_toml
    )
    archive = next(
        a for a in pack_package(pkg_dir, tmp_path / "archives") if a.name.endswith(".tar.gz")
    )
    pointer = f"/api/packages/{namespace}/generic/{name}/{version}/{archive.name}"
    return archive, tree_hash(pkg_dir), pointer


def resolved_pkg(namespace, name, version, content_hash, pointer, **kw):
    return {
        "namespace": namespace,
        "name": name,
        "version": version,
        "content_hash": content_hash,
        "download_pointer": pointer,
        "direct": kw.get("direct", True),
        "already_satisfied": kw.get("already_satisfied", False),
        "commands": kw.get("commands", {name: "src/main.py"}),
    }


def fake_server(monkeypatch, resolve_handler, blobs=None):
    """Route /resolve to ``resolve_handler`` and blob GETs to ``blobs`` (path ->
    bytes). Returns the recorded request list."""
    blobs = blobs or {}
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/resolve":
            return resolve_handler(request)
        body = blobs.get(request.url.path)
        if body is None:
            return httpx.Response(404, text="no such blob")
        return httpx.Response(200, content=body)

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        remote_install, "_client", lambda: httpx.Client(transport=transport)
    )
    return requests


def shim_path(home: Path, command: str) -> Path:
    return home / "bin" / (f"{command}.cmd" if os.name == "nt" else command)


def lockfile(home: Path) -> list[dict]:
    return json.loads((home / "installed.lock").read_text())["packages"]


# --- parse_target ----------------------------------------------------------


def test_parse_target_namespace_name():
    assert parse_target("acme/my-tool") == ("acme/my-tool", "")


def test_parse_target_with_spec():
    assert parse_target("acme/my-tool@^1.2") == ("acme/my-tool", "^1.2")


@pytest.mark.parametrize("target", ["my-tool", "acme", "a/b/c", "/name", "acme/"])
def test_parse_target_rejects_non_fully_namespaced(target):
    with pytest.raises(RemoteInstallError, match="fully-namespaced"):
        parse_target(target)


def test_parse_target_rejects_bad_spec():
    with pytest.raises(RemoteInstallError, match="invalid version spec"):
        parse_target("acme/my-tool@not a version")


# --- installed_closure -----------------------------------------------------


def test_installed_closure_is_remote_provenance_only():
    lock = {
        "packages": [
            {"namespace": "acme", "name": "a", "version": "1.0.0", "provenance": {"type": "remote"}},
            {"namespace": "acme", "name": "b", "version": "2.0.0", "provenance": {"type": "local"}},
            {"namespace": "infra", "name": "c", "version": "3.0.0", "provenance": {"type": "remote"}},
        ]
    }
    closure = installed_closure(lock)
    assert {(p.package, p.version) for p in closure} == {
        ("acme/a", "1.0.0"),
        ("infra/c", "3.0.0"),
    }


# --- resolve_root: remote selection ----------------------------------------


def _resolve_only(monkeypatch, per_remote):
    """Fake /resolve keyed by request host; per_remote maps host -> Response."""
    def handler(request: httpx.Request) -> httpx.Response:
        return per_remote[request.url.host]

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        remote_install, "_client", lambda: httpx.Client(transport=transport)
    )


def _empty_request():
    return ResolveRequest(root="acme/x", spec="", platform="linux", installed=[])


def test_resolve_root_stops_at_first_remote_hosting_the_root(monkeypatch):
    remotes = [Remote("a", "https://a.example"), Remote("b", "https://b.example")]
    body = {"packages": [resolved_pkg("acme", "x", "1.0.0", "sha256:x", "/p")], "tools": []}
    _resolve_only(
        monkeypatch,
        {"a.example": httpx.Response(404), "b.example": httpx.Response(200, json=body)},
    )
    remote, result = resolve_root(remotes, None, "acme/x", "", "linux", [])
    assert remote.name == "b"
    assert result.packages[0].version == "1.0.0"


def test_resolve_root_all_404_reports_tried_remotes(monkeypatch):
    remotes = [Remote("a", "https://a.example"), Remote("b", "https://b.example")]
    _resolve_only(
        monkeypatch,
        {"a.example": httpx.Response(404), "b.example": httpx.Response(404)},
    )
    with pytest.raises(RemoteInstallError, match="no configured remote has package .*tried: a, b"):
        resolve_root(remotes, None, "acme/x", "", "linux", [])


def test_resolve_root_422_is_a_resolve_error(monkeypatch):
    remotes = [Remote("a", "https://a.example")]
    _resolve_only(
        monkeypatch,
        {"a.example": httpx.Response(422, json={"detail": "'acme/c' no version satisfies"})},
    )
    with pytest.raises(ResolveError, match="cannot satisfy"):
        resolve_root(remotes, None, "acme/x", "", "linux", [])


def test_resolve_root_forced_remote_unknown_name(monkeypatch):
    remotes = [Remote("a", "https://a.example")]
    with pytest.raises(RemoteInstallError, match="no remote named 'nope'"):
        resolve_root(remotes, "nope", "acme/x", "", "linux", [])


def test_resolve_root_forced_remote_missing_root(monkeypatch):
    remotes = [Remote("a", "https://a.example")]
    _resolve_only(monkeypatch, {"a.example": httpx.Response(404)})
    with pytest.raises(RemoteInstallError, match="remote 'a' has no package"):
        resolve_root(remotes, "a", "acme/x", "", "linux", [])


def test_resolve_root_no_remotes_points_at_login(monkeypatch):
    with pytest.raises(RemoteInstallError, match="scripticus login"):
        resolve_root([], None, "acme/x", "", "linux", [])


# --- Full CLI flow ---------------------------------------------------------


def test_remote_install_resolves_downloads_verifies_and_installs(home, tmp_path, monkeypatch):
    archive, chash, pointer = make_package(tmp_path, "my-tool")
    save_remotes(home, [Remote("origin", URL)])
    set_token(home, URL, "tok")

    def resolve_handler(request):
        return httpx.Response(
            200,
            json={"packages": [resolved_pkg("acme", "my-tool", "0.1.0", chash, pointer)], "tools": []},
        )

    requests = fake_server(monkeypatch, resolve_handler, {pointer: archive.read_bytes()})

    result = runner.invoke(app, ["install", "acme/my-tool", "-y"])
    assert result.exit_code == 0, result.output

    assert (home / "pkgs" / "acme" / "my-tool" / "0.1.0" / "meta.toml").is_file()
    assert shim_path(home, "acme.my-tool.my-tool").is_file()
    assert shim_path(home, "acme.my-tool").is_file()
    assert shim_path(home, "my-tool").is_file()

    [entry] = lockfile(home)
    assert entry["version"] == "0.1.0"
    assert entry["content_hash"] == chash
    assert entry["direct"] is True
    assert entry["provenance"] == {"type": "remote", "remote": "origin", "url": URL}

    # The resolve request carried the platform and an (empty) installed closure.
    resolve_req = next(r for r in requests if r.url.path == "/resolve")
    body = json.loads(resolve_req.read())
    assert body["root"] == "acme/my-tool"
    assert body["installed"] == []
    # The blob was fetched with the stored token.
    blob_req = next(r for r in requests if r.url.path == pointer)
    assert blob_req.headers["Authorization"] == "token tok"


def test_remote_install_verifies_tree_hash_and_aborts_on_mismatch(home, tmp_path, monkeypatch):
    archive, _chash, pointer = make_package(tmp_path, "my-tool")
    save_remotes(home, [Remote("origin", URL)])
    set_token(home, URL, "tok")

    def resolve_handler(request):
        # Claim a hash the downloaded tree won't match.
        return httpx.Response(
            200,
            json={
                "packages": [resolved_pkg("acme", "my-tool", "0.1.0", "sha256:deadbeef", pointer)],
                "tools": [],
            },
        )

    fake_server(monkeypatch, resolve_handler, {pointer: archive.read_bytes()})

    result = runner.invoke(app, ["install", "acme/my-tool", "-y"])
    assert result.exit_code == 1
    assert "content hash mismatch" in result.output
    assert not (home / "pkgs").exists()  # stage-then-commit: nothing installed


def test_remote_install_transitive_closure_marks_direct_and_transitive(
    home, tmp_path, monkeypatch
):
    lib, lib_hash, lib_ptr = make_package(tmp_path, "lib", version="0.1.0")
    root_archive, root_hash, root_ptr = make_package(
        tmp_path,
        "app",
        version="0.1.0",
        extra_toml='\n[dependencies.packages]\n"acme/lib" = "^0.1"\n',
    )
    save_remotes(home, [Remote("origin", URL)])
    set_token(home, URL, "tok")

    def resolve_handler(request):
        return httpx.Response(
            200,
            json={
                "packages": [
                    resolved_pkg("acme", "lib", "0.1.0", lib_hash, lib_ptr, direct=False),
                    resolved_pkg("acme", "app", "0.1.0", root_hash, root_ptr, direct=True),
                ],
                "tools": [],
            },
        )

    fake_server(
        monkeypatch,
        resolve_handler,
        {lib_ptr: lib.read_bytes(), root_ptr: root_archive.read_bytes()},
    )

    result = runner.invoke(app, ["install", "acme/app", "-y"])
    assert result.exit_code == 0, result.output

    entries = {e["name"]: e for e in lockfile(home)}
    assert entries["lib"]["direct"] is False
    assert entries["app"]["direct"] is True
    assert entries["app"]["dependencies"] == {"acme/lib": "^0.1"}


def test_already_satisfied_root_is_nothing_to_do(home, tmp_path, monkeypatch):
    _archive, chash, pointer = make_package(tmp_path, "my-tool")
    save_remotes(home, [Remote("origin", URL)])
    set_token(home, URL, "tok")

    def resolve_handler(request):
        return httpx.Response(
            200,
            json={
                "packages": [
                    resolved_pkg("acme", "my-tool", "0.1.0", chash, pointer, already_satisfied=True)
                ],
                "tools": [],
            },
        )

    requests = fake_server(monkeypatch, resolve_handler)  # no blobs — none fetched
    result = runner.invoke(app, ["install", "acme/my-tool", "-y"])
    assert result.exit_code == 0, result.output
    assert "already installed" in result.output
    assert not any(r.url.path != "/resolve" for r in requests)  # no download


# --- Tools -----------------------------------------------------------------


def _only_missing(monkeypatch, missing_names):
    """shutil.which returns None for the named tools, a path otherwise."""
    monkeypatch.setattr(
        "shutil.which",
        lambda name: None if name in missing_names else f"/usr/bin/{name}",
    )


def test_remote_install_runs_configured_tool_installer_first(home, tmp_path, monkeypatch):
    archive, chash, pointer = make_package(tmp_path, "my-tool")
    save_remotes(home, [Remote("origin", URL)])
    set_token(home, URL, "tok")
    (home / "config.toml").write_text(
        f'[[remotes]]\nname = "origin"\nurl = "{URL}"\n\n'
        '[tools]\ninstall = "apt-get install -y {packages}"\nescalate = "sudo"\n'
    )
    _only_missing(monkeypatch, {"jq"})

    ran = {}
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda argv, *a, **k: ran.update(argv=argv) or subprocess.CompletedProcess(argv, 0),
    )

    def resolve_handler(request):
        return httpx.Response(
            200,
            json={
                "packages": [resolved_pkg("acme", "my-tool", "0.1.0", chash, pointer)],
                "tools": [{"name": "jq", "required": True}],
            },
        )

    fake_server(monkeypatch, resolve_handler, {pointer: archive.read_bytes()})

    result = runner.invoke(app, ["install", "acme/my-tool", "-y"])
    assert result.exit_code == 0, result.output
    shell = ["cmd", "/c"] if os.name == "nt" else ["bash", "-lc"]
    assert ran["argv"] == [*shell, "sudo apt-get install -y jq"]
    assert (home / "pkgs" / "acme" / "my-tool" / "0.1.0").is_dir()


def test_missing_required_tool_without_installer_refuses_before_download(
    home, tmp_path, monkeypatch
):
    archive, chash, pointer = make_package(tmp_path, "my-tool")
    save_remotes(home, [Remote("origin", URL)])
    set_token(home, URL, "tok")
    _only_missing(monkeypatch, {"jq"})

    def resolve_handler(request):
        return httpx.Response(
            200,
            json={
                "packages": [resolved_pkg("acme", "my-tool", "0.1.0", chash, pointer)],
                "tools": [{"name": "jq", "required": True}],
            },
        )

    requests = fake_server(monkeypatch, resolve_handler, {pointer: archive.read_bytes()})
    result = runner.invoke(app, ["install", "acme/my-tool", "-y"])
    assert result.exit_code == 1
    assert "missing required system tools: jq" in result.output
    assert not (home / "pkgs").exists()
    assert not any(r.url.path == pointer for r in requests)  # never downloaded


def test_skip_tools_installs_despite_missing_required_tool(home, tmp_path, monkeypatch):
    archive, chash, pointer = make_package(tmp_path, "my-tool")
    save_remotes(home, [Remote("origin", URL)])
    set_token(home, URL, "tok")
    _only_missing(monkeypatch, {"jq"})

    def boom(*a, **k):  # the installer must not run under --skip-tools
        raise AssertionError("tool installer should not run")

    monkeypatch.setattr(tools_module, "_run_shell", boom)

    def resolve_handler(request):
        return httpx.Response(
            200,
            json={
                "packages": [resolved_pkg("acme", "my-tool", "0.1.0", chash, pointer)],
                "tools": [{"name": "jq", "required": True}],
            },
        )

    fake_server(monkeypatch, resolve_handler, {pointer: archive.read_bytes()})
    result = runner.invoke(app, ["install", "acme/my-tool", "--skip-tools", "-y"])
    assert result.exit_code == 0, result.output
    assert (home / "pkgs" / "acme" / "my-tool" / "0.1.0").is_dir()


# --- Remote selection / auth via the CLI -----------------------------------


def test_remote_option_forces_a_specific_remote(home, tmp_path, monkeypatch):
    archive, chash, pointer = make_package(tmp_path, "my-tool")
    save_remotes(home, [Remote("origin", URL), Remote("public", "https://pub.example")])
    set_token(home, "https://pub.example", "tok-pub")

    def resolve_handler(request):
        assert request.url.host == "pub.example"
        return httpx.Response(
            200,
            json={"packages": [resolved_pkg("acme", "my-tool", "0.1.0", chash, pointer)], "tools": []},
        )

    fake_server(monkeypatch, resolve_handler, {pointer: archive.read_bytes()})
    result = runner.invoke(app, ["install", "acme/my-tool", "--remote", "public", "-y"])
    assert result.exit_code == 0, result.output
    assert lockfile(home)[0]["provenance"]["remote"] == "public"


def test_not_logged_in_is_actionable(home, tmp_path, monkeypatch):
    archive, chash, pointer = make_package(tmp_path, "my-tool")
    save_remotes(home, [Remote("origin", URL)])  # no token stored

    def resolve_handler(request):
        return httpx.Response(
            200,
            json={"packages": [resolved_pkg("acme", "my-tool", "0.1.0", chash, pointer)], "tools": []},
        )

    fake_server(monkeypatch, resolve_handler, {pointer: archive.read_bytes()})
    result = runner.invoke(app, ["install", "acme/my-tool", "-y"])
    assert result.exit_code == 1
    assert "not logged in to 'origin'" in result.output


def test_install_requires_exactly_one_of_package_or_file(home, tmp_path):
    existing = tmp_path / "x.tar.gz"
    existing.write_bytes(b"x")
    both = runner.invoke(app, ["install", "acme/my-tool", "-f", str(existing)])
    assert both.exit_code == 1
    assert "not both" in both.output
    neither = runner.invoke(app, ["install"])
    assert neither.exit_code == 1


# --- Shim conflicts across installed packages ------------------------------


def test_remote_install_reports_shim_conflict_with_outside_package(home, tmp_path, monkeypatch):
    # Pre-install a local package owning the bare 'clash' shim, then remote
    # install another package providing 'clash': -y must abort on the conflict.
    other = make_package(tmp_path, "other")[0]
    # give 'other' a command named 'clash' by installing it locally first
    local, _lh, _lp = make_package(
        tmp_path, "clash-owner", extra_toml='\n[commands]\nclash = "src/main.py"\n'
    )
    runner.invoke(app, ["install", "-f", str(local), "-y"])
    assert shim_path(home, "clash").is_file()

    archive, chash, pointer = make_package(
        tmp_path, "newtool", extra_toml='\n[commands]\nclash = "src/main.py"\n'
    )
    save_remotes(home, [Remote("origin", URL)])
    set_token(home, URL, "tok")

    def resolve_handler(request):
        return httpx.Response(
            200,
            json={
                "packages": [
                    resolved_pkg("acme", "newtool", "0.1.0", chash, pointer, commands={"clash": "src/main.py"})
                ],
                "tools": [],
            },
        )

    fake_server(monkeypatch, resolve_handler, {pointer: archive.read_bytes()})
    result = runner.invoke(app, ["install", "acme/newtool", "-y"])
    assert result.exit_code == 1
    assert "overwrite" in result.output.lower()
    # newtool was not installed (conflict aborted the transaction)
    assert not (home / "pkgs" / "acme" / "newtool").exists()
    _ = other  # unused archive kept for clarity
