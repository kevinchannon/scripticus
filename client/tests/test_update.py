"""`scripticus update` (D52/D53): target selection, remote grouping, the
shim-shrink and tool-advisory helpers, and the full install -> update CLI flow
against the fake server from the remote-install tests."""

import json

import httpx
import pytest
from typer.testing import CliRunner

from scripticus.cli import app
from scripticus.config import Remote, save_remotes
from scripticus.credentials import set_token
from scripticus.update import (
    UpdateError,
    dropped_convenience_shims,
    group_by_remote,
    select_targets,
)

runner = CliRunner()
URL = "https://reg.example.com"  # matches conftest.REG_URL


# --- select_targets --------------------------------------------------------


def _lock(*entries):
    return {"packages": list(entries)}


def _entry(namespace, name, version="1.0.0", provenance="remote", direct=True, remote="origin"):
    prov = {"type": provenance}
    if provenance == "remote":
        prov |= {"remote": remote, "url": URL}
    return {
        "namespace": namespace,
        "name": name,
        "version": version,
        "direct": direct,
        "provenance": prov,
        "commands": [name],
        "shims": [name, f"{namespace}.{name}"],
    }


def test_no_names_selects_direct_remote_packages():
    lock = _lock(
        _entry("acme", "a", direct=True),
        _entry("acme", "dep", direct=False),  # transitive: not a default target
        _entry("acme", "local", provenance="local"),
    )
    targets = select_targets(lock, [])
    assert [(e["namespace"], e["name"]) for e in targets.entries] == [("acme", "a")]
    assert targets.skipped_local == []


def test_named_local_target_is_skipped_with_a_note():
    lock = _lock(_entry("acme", "a"), _entry("acme", "loc", provenance="local"))
    targets = select_targets(lock, ["acme/loc"])
    assert targets.entries == []
    assert targets.skipped_local == ["acme/loc"]


def test_named_bare_target_resolves_unambiguously():
    lock = _lock(_entry("acme", "tool"))
    targets = select_targets(lock, ["tool"])
    assert [(e["namespace"], e["name"]) for e in targets.entries] == [("acme", "tool")]


def test_unknown_target_is_an_error():
    with pytest.raises(UpdateError, match="not installed"):
        select_targets(_lock(_entry("acme", "a")), ["acme/nope"])


def test_ambiguous_bare_target_is_an_error():
    lock = _lock(_entry("acme", "tool"), _entry("infra", "tool"))
    with pytest.raises(UpdateError, match="more than one"):
        select_targets(lock, ["tool"])


# --- group_by_remote -------------------------------------------------------


def test_group_by_remote_splits_targets_by_provenance():
    groups = group_by_remote(
        [
            _entry("acme", "a", remote="origin"),
            _entry("acme", "b", remote="origin"),
            _entry("infra", "c", remote="mirror"),
        ]
    )
    assert groups == {"origin": ["acme/a", "acme/b"], "mirror": ["infra/c"]}


# --- dropped_convenience_shims ---------------------------------------------


def test_dropped_shims_are_those_for_removed_commands():
    old = {"namespace": "acme", "name": "a", "shims": ["foo", "acme.foo", "bar", "acme.bar"]}
    # the new version keeps 'foo', drops 'bar'
    dropped = dropped_convenience_shims(old, {"foo": "src/foo.py"})
    assert dropped == ["acme.bar", "bar"]


def test_nothing_dropped_when_commands_are_unchanged():
    old = {"namespace": "acme", "name": "a", "shims": ["foo", "acme.foo"]}
    assert dropped_convenience_shims(old, {"foo": "src/foo.py"}) == []


# --- Full install -> update flow -------------------------------------------


def _install(home, make_package, fake_server, resolved_pkg, version="0.1.0"):
    archive, chash, pointer = make_package("my-tool", version=version)
    save_remotes(home, [Remote("origin", URL)])
    set_token(home, URL, "tok")

    def resolve_handler(request):
        return httpx.Response(
            200,
            json={
                "packages": [resolved_pkg("acme", "my-tool", version, chash, pointer)],
                "tools": [],
            },
        )

    fake_server(resolve_handler, {pointer: archive.read_bytes()})
    assert runner.invoke(app, ["install", "acme/my-tool", "-y"]).exit_code == 0


def test_update_installs_the_newer_version(
    home, make_package, fake_server, resolved_pkg, lockfile
):
    _install(home, make_package, fake_server, resolved_pkg, version="0.1.0")

    new_archive, new_hash, new_pointer = make_package("my-tool", version="0.2.0")

    def resolve_handler(request):
        return httpx.Response(
            200,
            json={
                "packages": [resolved_pkg("acme", "my-tool", "0.2.0", new_hash, new_pointer)],
                "tools": [],
            },
        )

    requests = fake_server(resolve_handler, {new_pointer: new_archive.read_bytes()})

    result = runner.invoke(app, ["update", "-y"])
    assert result.exit_code == 0, result.output

    [entry] = lockfile(home)
    assert entry["version"] == "0.2.0"
    assert entry["content_hash"] == new_hash
    assert (home / "pkgs" / "acme" / "my-tool" / "0.2.0" / "meta.toml").is_file()
    assert not (home / "pkgs" / "acme" / "my-tool" / "0.1.0").exists()

    # update floats its target: upgrade=True, target as a root, the installed
    # closure sent so non-targets stay put (D52).
    resolve_req = next(r for r in requests if r.url.path == "/resolve")
    body = json.loads(resolve_req.read())
    assert body["upgrade"] is True
    assert body["roots"] == [{"package": "acme/my-tool", "spec": ""}]
    assert body["installed"] == [{"package": "acme/my-tool", "version": "0.1.0"}]


def test_update_reports_up_to_date_when_nothing_moves(
    home, make_package, fake_server, resolved_pkg
):
    _install(home, make_package, fake_server, resolved_pkg, version="0.1.0")

    def resolve_handler(request):
        return httpx.Response(
            200,
            json={
                "packages": [
                    resolved_pkg(
                        "acme", "my-tool", "0.1.0", "sha256:x", "/p", already_satisfied=True
                    )
                ],
                "tools": [],
            },
        )

    fake_server(resolve_handler)
    result = runner.invoke(app, ["update", "-y"])
    assert result.exit_code == 0
    assert "up to date" in result.output.lower()


def test_update_reports_a_held_back_target(home, make_package, fake_server, resolved_pkg):
    _install(home, make_package, fake_server, resolved_pkg, version="0.1.0")

    def resolve_handler(request):
        pkg = resolved_pkg(
            "acme", "my-tool", "0.1.0", "sha256:x", "/p", already_satisfied=True
        )
        pkg["held_back"] = {
            "available": "0.2.0",
            "blocked_by": "acme/app",
            "detail": "no version satisfies ['^0.1']",
        }
        return httpx.Response(200, json={"packages": [pkg], "tools": []})

    fake_server(resolve_handler)
    result = runner.invoke(app, ["update", "-y"])
    assert result.exit_code == 0
    assert "held at 0.1.0" in result.output
    assert "0.2.0 available" in result.output
    assert "acme/app" in result.output


def _install_one(
    home, fake_server, resolved_pkg, archive, pkg, version, chash, pointer, commands,
    accept="-y",
):
    def resolve_handler(request):
        return httpx.Response(
            200,
            json={
                "packages": [
                    resolved_pkg("acme", pkg, version, chash, pointer, commands=commands)
                ],
                "tools": [],
            },
        )

    fake_server(resolve_handler, {pointer: archive.read_bytes()})
    flags = accept.split()
    assert runner.invoke(app, ["install", f"acme/{pkg}", *flags]).exit_code == 0


def test_update_reconciles_a_dropped_convenience_shim(
    home, make_package, fake_server, resolved_pkg, lockfile, shim_path
):
    save_remotes(home, [Remote("origin", URL)])
    set_token(home, URL, "tok")

    # A provider of the 'extra' command, installed first...
    extra_archive, extra_hash, extra_pointer = make_package("extra")
    _install_one(
        home, fake_server, resolved_pkg, extra_archive, "extra", "0.1.0",
        extra_hash, extra_pointer, {"extra": "src/main.py"},
    )
    # ...then my-tool, which provides both 'my-tool' and 'extra' — last-wins,
    # so my-tool now owns the 'extra' convenience shims.
    v1, v1_hash, v1_pointer = make_package("my-tool", version="0.1.0")
    _install_one(
        home, fake_server, resolved_pkg, v1, "my-tool", "0.1.0", v1_hash, v1_pointer,
        {"my-tool": "src/main.py", "extra": "src/main.py"},
        accept="--force all",  # take over the 'extra' shim from the provider
    )
    assert shim_path(home, "extra").is_file()
    my_tool = next(e for e in lockfile(home) if e["name"] == "my-tool")
    assert "extra" in my_tool["shims"]

    # Update my-tool to a version that drops the 'extra' command.
    v2, v2_hash, v2_pointer = make_package("my-tool", version="0.2.0")

    def resolve_handler(request):
        return httpx.Response(
            200,
            json={
                "packages": [
                    resolved_pkg("acme", "my-tool", "0.2.0", v2_hash, v2_pointer,
                                 commands={"my-tool": "src/main.py"})
                ],
                "tools": [],
            },
        )

    fake_server(resolve_handler, {v2_pointer: v2.read_bytes()})
    result = runner.invoke(app, ["update", "my-tool", "-y"])
    assert result.exit_code == 0, result.output

    # The orphaned 'extra' shim is gone, and its still-installed provider is
    # offered as a replacement (non-interactively, just reported — D53).
    assert not shim_path(home, "extra").is_file()
    my_tool = next(e for e in lockfile(home) if e["name"] == "my-tool")
    assert "extra" not in my_tool["shims"]
    assert "acme/extra" in result.output
    assert "scripticus use" in result.output


def test_update_advises_on_a_tool_the_closure_no_longer_needs(
    home, make_package, fake_server, resolved_pkg
):
    save_remotes(home, [Remote("origin", URL)])
    set_token(home, URL, "tok")

    # v1 declares an optional tool in its manifest; v2 drops it.
    v1, v1_hash, v1_pointer = make_package(
        "my-tool", version="0.1.0",
        extra_toml='\n[dependencies.tools]\noptional = ["faketool"]\n',
    )
    _install_one(
        home, fake_server, resolved_pkg, v1, "my-tool", "0.1.0", v1_hash, v1_pointer,
        {"my-tool": "src/main.py"},
    )

    v2, v2_hash, v2_pointer = make_package("my-tool", version="0.2.0")

    def resolve_handler(request):
        return httpx.Response(
            200,
            json={
                "packages": [
                    resolved_pkg("acme", "my-tool", "0.2.0", v2_hash, v2_pointer,
                                 commands={"my-tool": "src/main.py"})
                ],
                "tools": [],
            },
        )

    fake_server(resolve_handler, {v2_pointer: v2.read_bytes()})
    result = runner.invoke(app, ["update", "-y"])
    assert result.exit_code == 0, result.output
    assert "faketool" in result.output
    assert "package manager" in result.output


def test_update_skips_a_locally_installed_package(home, make_package):
    archive, _, _ = make_package("local-tool")
    # A plain local install records local provenance.
    assert runner.invoke(app, ["install", "-f", str(archive), "-y"]).exit_code == 0

    save_remotes(home, [Remote("origin", URL)])
    result = runner.invoke(app, ["update", "acme/local-tool"])
    assert result.exit_code == 0
    assert "skipping" in result.output.lower()
    assert "cannot update" in result.output.lower()
