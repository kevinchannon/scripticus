import json
import os
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
    extra_toml: str = "",
) -> Path:
    workdir = Path(tempfile.mkdtemp(dir=parent, prefix="pkgsrc-"))
    scaffold_package("python", name, namespace, workdir)
    manifest = workdir / name / "meta.toml"
    manifest.write_text(manifest.read_text() + extra_toml)
    return pack_package(workdir / name, parent / "archives")[0]


def install(archive: Path) -> None:
    result = runner.invoke(app, ["install", "-f", str(archive), "-y"])
    assert result.exit_code == 0, result.output


def shim_path(home: Path, command: str) -> Path:
    return home / "bin" / (f"{command}.cmd" if os.name == "nt" else command)


def lockfile(home: Path) -> dict:
    return json.loads((home / "installed.lock").read_text())


def test_uninstall_removes_files_shim_and_lockfile_entry(home, tmp_path):
    install(build_archive(tmp_path))

    result = runner.invoke(app, ["uninstall", "my-tool", "-y"])
    assert result.exit_code == 0, result.output
    assert "Uninstalled acme/my-tool 0.1.0" in result.output

    assert not shim_path(home, "my-tool").exists()
    assert not (home / "pkgs" / "acme").exists()
    assert lockfile(home)["packages"] == []


def test_namespaced_form_is_accepted(home, tmp_path):
    install(build_archive(tmp_path))

    result = runner.invoke(app, ["uninstall", "acme/my-tool", "-y"])
    assert result.exit_code == 0, result.output
    assert lockfile(home)["packages"] == []


def test_uninstalling_a_package_that_is_not_installed_is_an_error(home, tmp_path):
    result = runner.invoke(app, ["uninstall", "no-such-tool", "-y"])
    assert result.exit_code == 1
    assert "not installed" in result.output


def test_interactive_decline_removes_nothing(home, tmp_path):
    install(build_archive(tmp_path))

    result = runner.invoke(app, ["uninstall", "my-tool"], input="n\n")
    assert result.exit_code == 1
    assert "Aborted" in result.output
    assert shim_path(home, "my-tool").is_file()
    assert len(lockfile(home)["packages"]) == 1


def test_interactive_accept_removes(home, tmp_path):
    install(build_archive(tmp_path))

    result = runner.invoke(app, ["uninstall", "my-tool"], input="y\n")
    assert result.exit_code == 0, result.output
    assert not shim_path(home, "my-tool").exists()


def test_bare_name_matching_two_namespaces_is_an_error(home, tmp_path):
    install(build_archive(tmp_path, namespace="acme"))
    # A distinct command name, so the two same-named packages don't clash on shims.
    install(
        build_archive(
            tmp_path,
            namespace="globex",
            extra_toml='\n[commands]\nglobex-tool = "src/main.py"\n',
        )
    )

    result = runner.invoke(app, ["uninstall", "my-tool", "-y"])
    assert result.exit_code == 1
    assert "more than one" in result.output
    assert "acme/my-tool" in result.output
    assert "globex/my-tool" in result.output
    assert len(lockfile(home)["packages"]) == 2

    result = runner.invoke(app, ["uninstall", "globex/my-tool", "-y"])
    assert result.exit_code == 0, result.output
    assert [e["namespace"] for e in lockfile(home)["packages"]] == ["acme"]


def test_shims_taken_over_by_another_package_are_left_alone(home, tmp_path):
    clash = '\n[commands]\nclash = "src/main.py"\n'
    install(build_archive(tmp_path, name="tool-one", extra_toml=clash))
    result = runner.invoke(
        app,
        ["install", "-f", str(build_archive(tmp_path, name="tool-two", extra_toml=clash)), "--force", "all"],
    )
    assert result.exit_code == 0, result.output

    result = runner.invoke(app, ["uninstall", "tool-one", "-y"])
    assert result.exit_code == 0, result.output
    assert "No command shims to remove" in result.output

    assert "tool-two" in shim_path(home, "clash").read_text()
    assert [e["name"] for e in lockfile(home)["packages"]] == ["tool-two"]
