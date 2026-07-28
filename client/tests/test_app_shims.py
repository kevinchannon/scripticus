"""Commands named `app`, which macOS will not execute (D11/D38).

Recent macOS (observed on 26, but not on the older macOS the GitHub runners use)
kills any process whose executable is a regular file named `*.app` — it takes
the path for an application bundle. A command called `app` loses *all three* of
its shim tiers there: the two dotted ones end in `.app`, and the bare `app` dies
with them because it delegates to the fully-qualified shim. `install.py` works
around it by writing the real script to a sidecar and leaving a symlink at the
name the user types.

These tests **run** the shims rather than inspecting them: the workaround leans
on macOS checking the name of the file exec resolves to, which is observed
behaviour rather than a documented contract. If Apple ever changes it, this is
what says so.
"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

from scripticus.cli import app as cli
from scripticus.install import SIDECAR_SUFFIX
from scripticus.pack import pack_package

runner = CliRunner()

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX shims")

MANIFEST = """\
[package]
namespace = "acme"
name = "{name}"
version = "1.0.0"
language = "bash"
description = "A command called app"

[platforms]
os = ["linux", "macos", "windows"]

[commands]
{command} = "src/main.sh"
"""


@pytest.fixture
def home(tmp_path, monkeypatch):
    home_dir = tmp_path / "scripticus-home"
    monkeypatch.setenv("SCRIPTICUS_HOME", str(home_dir))
    monkeypatch.chdir(tmp_path)
    return home_dir


def build(parent: Path, name: str = "my-tool", command: str = "app") -> Path:
    workdir = Path(tempfile.mkdtemp(dir=parent, prefix="pkgsrc-"))
    package_dir = workdir / name
    (package_dir / "src").mkdir(parents=True)
    (package_dir / "meta.toml").write_text(MANIFEST.format(name=name, command=command))
    (package_dir / "src" / "main.sh").write_text('#!/usr/bin/env bash\necho "ran app"\n')
    return pack_package(package_dir, parent / "archives")[0]


def shim(home: Path, name: str) -> Path:
    return home / "bin" / name


def test_every_tier_of_a_command_named_app_actually_runs(home, tmp_path):
    # The whole point: all three tiers execute. On macOS two of them would be
    # SIGKILLed without the sidecar indirection.
    result = runner.invoke(cli, ["install", "-f", str(build(tmp_path)), "-y"])
    assert result.exit_code == 0, result.output

    for tier in ("app", "acme.app", "acme.my-tool.app"):
        completed = subprocess.run(
            [str(shim(home, tier))], capture_output=True, text=True
        )
        assert completed.returncode == 0, f"{tier}: rc={completed.returncode} {completed.stderr}"
        assert completed.stdout.strip() == "ran app", tier


def test_a_command_named_app_is_runnable_through_PATH(home, tmp_path):
    # Same claim, reached the way a user reaches it.
    runner.invoke(cli, ["install", "-f", str(build(tmp_path)), "-y"])

    environment = {**os.environ, "PATH": f"{home / 'bin'}{os.pathsep}{os.environ['PATH']}"}
    completed = subprocess.run(
        ["acme.my-tool.app"], capture_output=True, text=True, env=environment
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "ran app"


@pytest.mark.skipif(sys.platform != "darwin", reason="the workaround is macOS-only")
def test_the_dotted_tiers_are_symlinks_to_sidecars_on_macos(home, tmp_path):
    runner.invoke(cli, ["install", "-f", str(build(tmp_path)), "-y"])

    for tier in ("acme.app", "acme.my-tool.app"):
        path = shim(home, tier)
        assert path.is_symlink(), tier
        sidecar = path.with_name(path.name + SIDECAR_SUFFIX)
        assert sidecar.is_file(), tier
        # Relative, so moving the bin dir does not break the pair.
        assert os.readlink(path) == sidecar.name

    # The bare name has no `.app` suffix, so it is left completely alone.
    assert not shim(home, "app").is_symlink()


@pytest.mark.skipif(sys.platform != "darwin", reason="the restriction is macOS-only")
def test_the_app_bundle_restriction_this_works_around(tmp_path):
    """Record whether *this* macOS refuses to exec a `*.app` file.

    Not every version does: macOS 26 kills it, while the older macOS on the
    GitHub runners runs it happily. So this cannot assert the breakage
    unconditionally — it documents the premise on a machine that has it, and
    skips where the indirection is merely harmless. The tests above are the
    ones that must pass everywhere.
    """
    script = tmp_path / "probe.app"
    script.write_text("#!/bin/sh\necho hi\n")
    script.chmod(0o755)

    completed = subprocess.run([str(script)], capture_output=True)

    if completed.returncode == 0:
        pytest.skip(
            "this macOS executes *.app scripts, so the sidecar indirection is a"
            " no-op here; it is still needed on versions that do not (macOS 26)"
        )
    assert completed.returncode == -9, (
        f"unexpected outcome for a *.app script: rc={completed.returncode}"
    )


def test_uninstall_leaves_no_sidecar_behind(home, tmp_path):
    runner.invoke(cli, ["install", "-f", str(build(tmp_path)), "-y"])
    result = runner.invoke(cli, ["uninstall", "acme/my-tool", "-y"])
    assert result.exit_code == 0, result.output

    assert list((home / "bin").iterdir()) == []


def test_a_shrinking_command_set_takes_its_sidecar_with_it(home, tmp_path):
    # Upgrading to a version without the `app` command must clean up both the
    # shim and whatever is behind it.
    runner.invoke(cli, ["install", "-f", str(build(tmp_path)), "-y"])
    replacement = build(tmp_path, command="other")
    runner.invoke(cli, ["install", "-f", str(replacement), "-y"])

    remaining = sorted(p.name for p in (home / "bin").iterdir())
    assert not any(name.startswith("acme.my-tool.app") for name in remaining), remaining
    assert not any(name.startswith("acme.app") for name in remaining), remaining
    assert "acme.my-tool.other" in remaining


def test_an_ordinary_command_gets_no_sidecar(home, tmp_path):
    runner.invoke(cli, ["install", "-f", str(build(tmp_path, command="normal")), "-y"])

    names = sorted(p.name for p in (home / "bin").iterdir())
    assert names == ["acme.my-tool.normal", "acme.normal", "normal"]
