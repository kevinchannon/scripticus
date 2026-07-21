import json
import os
import subprocess
import tempfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

from scripticus.cli import app
from scripticus.pack import pack_package
from scripticus.scaffold import scaffold_package

runner = CliRunner()


@pytest.fixture
def home(tmp_path, monkeypatch):
    home_dir = tmp_path / "scripticus-home"
    monkeypatch.setenv("SCRIPTICUS_HOME", str(home_dir))
    monkeypatch.chdir(tmp_path)
    return home_dir


def build_archive(
    parent: Path,
    name: str = "my-tool",
    namespace: str = "acme",
    language: str = "python",
    version: str = "0.1.0",
    extra_toml: str = "",
) -> Path:
    """Scaffold, adjust, and pack a package; return the first archive."""
    workdir = Path(tempfile.mkdtemp(dir=parent, prefix="pkgsrc-"))
    scaffold_package(language, name, namespace, workdir)
    manifest = workdir / name / "meta.toml"
    text = manifest.read_text().replace('version = "0.1.0"', f'version = "{version}"')
    manifest.write_text(text + extra_toml)
    return pack_package(workdir / name, parent / "archives")[0]


def shim_path(home: Path, command: str) -> Path:
    return home / "bin" / (f"{command}.cmd" if os.name == "nt" else command)


def lockfile(home: Path) -> dict:
    return json.loads((home / "installed.lock").read_text())


def test_install_creates_files_shim_and_lockfile(home, tmp_path):
    archive = build_archive(tmp_path)

    result = runner.invoke(app, ["install", "-f", str(archive), "-y"])
    assert result.exit_code == 0, result.output

    assert (home / "pkgs" / "acme" / "my-tool" / "0.1.0" / "meta.toml").is_file()
    # All three tiers (D38): fully-qualified plus two conveniences.
    assert shim_path(home, "acme.my-tool.my-tool").is_file()
    assert shim_path(home, "acme.my-tool").is_file()
    assert shim_path(home, "my-tool").is_file()

    [entry] = lockfile(home)["packages"]
    assert entry["namespace"] == "acme"
    assert entry["name"] == "my-tool"
    assert entry["version"] == "0.1.0"
    assert entry["content_hash"].startswith("sha256:")
    assert entry["commands"] == ["my-tool"]
    assert entry["shims"] == ["acme.my-tool", "my-tool"]
    assert entry["direct"] is True
    assert entry["provenance"]["type"] == "local"
    assert Path(entry["provenance"]["source"]) == archive.resolve()


@pytest.mark.skipif(os.name == "nt", reason="POSIX shim")
def test_all_three_tiers_run_and_conveniences_delegate(home, tmp_path):
    runner.invoke(app, ["install", "-f", str(build_archive(tmp_path)), "-y"])

    for tier in ("my-tool", "acme.my-tool", "acme.my-tool.my-tool"):
        completed = subprocess.run(
            [str(shim_path(home, tier))], capture_output=True, text=True
        )
        assert completed.returncode == 0
        assert completed.stdout.strip() == "Hello from my-tool!"

    # Conveniences delegate one hop to the fully-qualified shim, not the script.
    fq = shim_path(home, "acme.my-tool.my-tool")
    assert fq.name in shim_path(home, "my-tool").read_text()
    assert fq.name in shim_path(home, "acme.my-tool").read_text()


@pytest.mark.skipif(os.name == "nt", reason="POSIX shim")
def test_installed_shim_runs(home, tmp_path):
    archive = build_archive(tmp_path)
    runner.invoke(app, ["install", "-f", str(archive), "-y"])

    completed = subprocess.run(
        [str(shim_path(home, "my-tool"))], capture_output=True, text=True
    )
    assert completed.returncode == 0
    assert completed.stdout.strip() == "Hello from my-tool!"


def test_transaction_summary_is_shown(home, tmp_path):
    archive = build_archive(tmp_path)

    result = runner.invoke(app, ["install", "-f", str(archive), "-y"])
    assert "Installing acme/my-tool 0.1.0" in result.output
    assert "New packages:" in result.output
    assert "commands: my-tool" in result.output


def test_interactive_decline_installs_nothing(home, tmp_path):
    archive = build_archive(tmp_path)

    result = runner.invoke(app, ["install", "-f", str(archive)], input="n\n")
    assert result.exit_code == 1
    assert "Aborted" in result.output
    assert not shim_path(home, "my-tool").exists()
    assert not (home / "installed.lock").exists()


def test_interactive_accept_installs(home, tmp_path):
    archive = build_archive(tmp_path)

    result = runner.invoke(app, ["install", "-f", str(archive)], input="y\n")
    assert result.exit_code == 0, result.output
    assert shim_path(home, "my-tool").is_file()


def test_shim_conflict_aborts_whole_transaction_with_yes(home, tmp_path):
    clash = '\n[commands]\nclash = "src/main.py"\n'
    first = build_archive(tmp_path, name="tool-one", extra_toml=clash)
    second = build_archive(tmp_path, name="tool-two", extra_toml=clash)
    runner.invoke(app, ["install", "-f", str(first), "-y"])

    result = runner.invoke(app, ["install", "-f", str(second), "-y"])
    assert result.exit_code == 1
    assert "overwrite" in result.output

    entries = lockfile(home)["packages"]
    assert [e["name"] for e in entries] == ["tool-one"]
    assert "tool-one" in shim_path(home, "clash").read_text()


def test_shim_conflict_overwrites_with_force_all(home, tmp_path):
    # Same namespace, so both convenience tiers (clash and acme.clash) collide.
    clash = '\n[commands]\nclash = "src/main.py"\n'
    first = build_archive(tmp_path, name="tool-one", extra_toml=clash)
    second = build_archive(tmp_path, name="tool-two", extra_toml=clash)
    runner.invoke(app, ["install", "-f", str(first), "-y"])

    result = runner.invoke(app, ["install", "-f", str(second), "--force", "all"])
    assert result.exit_code == 0, result.output
    assert "Overwritten shims" in result.output
    assert "acme.clash" in result.output and "clash" in result.output

    by_name = {e["name"]: e for e in lockfile(home)["packages"]}
    # `commands` is what each provides (unchanged); `shims` is who owns them.
    assert by_name["tool-one"]["commands"] == by_name["tool-two"]["commands"] == ["clash"]
    assert by_name["tool-two"]["shims"] == ["acme.clash", "clash"]
    assert by_name["tool-one"]["shims"] == []
    # The winner's conveniences delegate to its own fully-qualified shim...
    assert "acme.tool-two.clash" in shim_path(home, "clash").read_text()
    # ...but the loser's fully-qualified shim still exists and is untouched.
    assert shim_path(home, "acme.tool-one.clash").is_file()


def test_cross_namespace_collision_contests_only_the_bare_shim(home, tmp_path):
    """Two namespaces providing the same command name: the namespaced shims
    differ (foo.clash vs bar.clash), so only bare `clash` collides — which
    is why -y would abort and --force all is needed for the second."""
    clash = '\n[commands]\nclash = "src/main.py"\n'
    first = build_archive(tmp_path, name="alpha", namespace="foo", extra_toml=clash)
    second = build_archive(tmp_path, name="beta", namespace="bar", extra_toml=clash)
    runner.invoke(app, ["install", "-f", str(first), "-y"])

    result = runner.invoke(app, ["install", "-f", str(second), "--force", "all"])
    assert result.exit_code == 0, result.output
    # Only the bare `clash` was overwritten; the namespaced shims never clash.
    assert "clash" in result.output and "foo.clash" not in result.output

    by_ns = {e["namespace"]: e for e in lockfile(home)["packages"]}
    assert by_ns["foo"]["shims"] == ["foo.clash"]
    assert by_ns["bar"]["shims"] == ["bar.clash", "clash"]
    assert shim_path(home, "foo.clash").is_file()
    assert shim_path(home, "bar.clash").is_file()


@pytest.mark.skipif(os.name == "nt", reason="POSIX shim")
def test_losers_fully_qualified_shim_still_runs_the_loser(home, tmp_path):
    """The D38 guarantee: losing the bare name never makes a command
    unreachable — its fully-qualified shim still runs it."""
    body = '\n[commands]\nclash = "src/main.py"\n'
    runner.invoke(
        app, ["install", "-f", str(build_archive(tmp_path, name="loser", extra_toml=body)), "-y"]
    )
    runner.invoke(
        app,
        ["install", "-f", str(build_archive(tmp_path, name="winner", extra_toml=body)), "--force", "all"],
    )

    def run(tier):
        return subprocess.run(
            [str(shim_path(home, tier))], capture_output=True, text=True
        ).stdout.strip()

    assert run("clash") == "Hello from winner!"  # bare went to the winner
    assert run("acme.loser.clash") == "Hello from loser!"  # loser still reachable
    assert run("acme.winner.clash") == "Hello from winner!"


def test_upgrade_replaces_previous_version(home, tmp_path):
    runner.invoke(app, ["install", "-f", str(build_archive(tmp_path, version="0.1.0")), "-y"])

    result = runner.invoke(
        app, ["install", "-f", str(build_archive(tmp_path, version="0.2.0")), "-y"]
    )
    assert result.exit_code == 0, result.output
    assert "Version changes:" in result.output
    assert "0.1.0 -> 0.2.0" in result.output

    [entry] = lockfile(home)["packages"]
    assert entry["version"] == "0.2.0"
    assert (home / "pkgs" / "acme" / "my-tool" / "0.2.0").is_dir()
    assert not (home / "pkgs" / "acme" / "my-tool" / "0.1.0").exists()


def test_downgrade_is_called_out(home, tmp_path):
    runner.invoke(app, ["install", "-f", str(build_archive(tmp_path, version="0.2.0")), "-y"])

    result = runner.invoke(
        app, ["install", "-f", str(build_archive(tmp_path, version="0.1.0")), "-y"]
    )
    assert result.exit_code == 0, result.output
    assert "downgrade" in result.output
    [entry] = lockfile(home)["packages"]
    assert entry["version"] == "0.1.0"


def test_reinstalling_same_content_is_a_noop(home, tmp_path):
    archive = build_archive(tmp_path)
    runner.invoke(app, ["install", "-f", str(archive), "-y"])

    result = runner.invoke(app, ["install", "-f", str(archive), "-y"])
    assert result.exit_code == 0, result.output
    assert "already installed" in result.output
    assert len(lockfile(home)["packages"]) == 1


@pytest.mark.skipif(os.name == "nt", reason="needs a non-Windows machine")
def test_platform_mismatch_is_an_error(home, tmp_path):
    archive = build_archive(tmp_path, language="powershell")  # targets windows only

    result = runner.invoke(app, ["install", "-f", str(archive), "-y"])
    assert result.exit_code == 1
    assert "this machine" in result.output
    assert not (home / "installed.lock").exists()


def test_missing_required_tool_is_reported(home, tmp_path):
    tools = '\n[dependencies.tools]\nrequires = ["definitely-not-a-real-tool-xyz"]\n'
    archive = build_archive(tmp_path, extra_toml=tools)

    result = runner.invoke(app, ["install", "-f", str(archive)], input="n\n")
    assert "NOT FOUND" in result.output
    assert "definitely-not-a-real-tool-xyz" in result.output


def test_declared_package_deps_are_an_error_until_the_resolver_exists(home, tmp_path):
    deps = '\n[dependencies.packages]\n"infra/log-common" = "^2.0"\n'
    archive = build_archive(tmp_path, extra_toml=deps)

    result = runner.invoke(app, ["install", "-f", str(archive), "-y"])
    assert result.exit_code == 1
    assert "cannot resolve package dependencies" in result.output
    assert "infra/log-common" in result.output
    assert not (home / "installed.lock").exists()


def test_archive_without_manifest_is_an_error(home, tmp_path):
    import tarfile

    bogus_dir = tmp_path / "bogus"
    (bogus_dir / "stuff").mkdir(parents=True)
    (bogus_dir / "stuff" / "file.txt").write_text("hello")
    archive = tmp_path / "bogus-0.0.1.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(bogus_dir / "stuff", arcname="stuff")

    result = runner.invoke(app, ["install", "-f", str(archive), "-y"])
    assert result.exit_code == 1
    assert "no meta.toml" in result.output


def test_unsupported_archive_format_is_an_error(home, tmp_path):
    not_archive = tmp_path / "file.txt"
    not_archive.write_text("hello")

    result = runner.invoke(app, ["install", "-f", str(not_archive), "-y"])
    assert result.exit_code == 1
    assert "not a supported archive" in result.output
