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


def install(archive: Path, force_all: bool = False) -> None:
    args = ["install", "-f", str(archive)] + (["--force", "all"] if force_all else ["-y"])
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output


def install_clash_providers(tmp_path, count: int) -> None:
    """Install packages tool-1..tool-N all providing 'clash'; tool-N owns it."""
    clash = '\n[commands]\nclash = "src/main.py"\n'
    for n in range(1, count + 1):
        archive = build_archive(tmp_path, name=f"tool-{n}", extra_toml=clash)
        install(archive, force_all=n > 1)


def shim_path(home: Path, command: str) -> Path:
    return home / "bin" / (f"{command}.cmd" if os.name == "nt" else command)


def lockfile(home: Path) -> dict:
    return json.loads((home / "installed.lock").read_text())


def test_use_repoints_shim_and_ownership(home, tmp_path):
    install_clash_providers(tmp_path, 2)

    result = runner.invoke(app, ["use", "acme/tool-1", "clash"])
    assert result.exit_code == 0, result.output
    assert "'clash' now points at acme/tool-1 0.1.0 (was acme/tool-2 0.1.0)" in result.output

    assert "tool-1" in shim_path(home, "clash").read_text()
    by_name = {e["name"]: e for e in lockfile(home)["packages"]}
    assert by_name["tool-1"]["commands"] == ["clash"]
    assert by_name["tool-2"]["commands"] == []


def test_use_accepts_bare_name(home, tmp_path):
    install_clash_providers(tmp_path, 2)

    result = runner.invoke(app, ["use", "tool-1", "clash"])
    assert result.exit_code == 0, result.output
    assert "tool-1" in shim_path(home, "clash").read_text()


def test_use_restores_an_orphaned_command(home, tmp_path):
    install_clash_providers(tmp_path, 2)
    result = runner.invoke(app, ["uninstall", "tool-2", "-y"])
    assert result.exit_code == 0, result.output
    assert not shim_path(home, "clash").exists()

    result = runner.invoke(app, ["use", "tool-1", "clash"])
    assert result.exit_code == 0, result.output
    assert "(previously had no shim)" in result.output
    assert "tool-1" in shim_path(home, "clash").read_text()
    [entry] = lockfile(home)["packages"]
    assert entry["commands"] == ["clash"]


def test_use_on_the_current_owner_is_a_noop(home, tmp_path):
    install_clash_providers(tmp_path, 2)
    before = shim_path(home, "clash").read_text()

    result = runner.invoke(app, ["use", "tool-2", "clash"])
    assert result.exit_code == 0, result.output
    assert "already points at acme/tool-2" in result.output
    assert shim_path(home, "clash").read_text() == before


def test_use_with_a_package_that_is_not_installed_is_an_error(home, tmp_path):
    result = runner.invoke(app, ["use", "no-such-tool", "clash"])
    assert result.exit_code == 1
    assert "not installed" in result.output


def test_use_with_an_ambiguous_bare_name_is_an_error(home, tmp_path):
    install(build_archive(tmp_path, namespace="acme"))
    install(
        build_archive(
            tmp_path,
            namespace="globex",
            extra_toml='\n[commands]\nglobex-tool = "src/main.py"\n',
        )
    )

    result = runner.invoke(app, ["use", "my-tool", "my-tool"])
    assert result.exit_code == 1
    assert "more than one" in result.output


def test_use_with_a_command_the_package_does_not_provide_is_an_error(home, tmp_path):
    install(build_archive(tmp_path))

    result = runner.invoke(app, ["use", "my-tool", "other-command"])
    assert result.exit_code == 1
    unwrapped = " ".join(result.output.split())
    assert "does not provide a command 'other-command'" in unwrapped
    assert "it provides: my-tool" in unwrapped
