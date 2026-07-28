"""Installing, staging and removing library packages (D57).

Where `snip` reads a snippet out of the installed tree, a library is *sourced*
out of it — so the tests that matter here run real shells: a bash command must
find `scr_load` in scope without asking for it, and a staged library must be
loadable by name.
"""

import json
import os
import subprocess
import tempfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

from scripticus import libraries
from scripticus.cli import app
from scripticus.pack import pack_package

runner = CliRunner()

LIBRARY_MANIFEST = """\
[package]
namespace = "{namespace}"
name = "{name}"
version = "{version}"
language = "{language}"
description = "String helpers"

[platforms]
os = ["linux", "macos"]

[library]
"""

LOAD_SCRIPT = """\
scr_shout() { echo "SHOUT:$1"; }
"""


@pytest.fixture
def home(tmp_path, monkeypatch):
    home_dir = tmp_path / "scripticus-home"
    monkeypatch.setenv("SCRIPTICUS_HOME", str(home_dir))
    monkeypatch.chdir(tmp_path)
    return home_dir


def build_library(
    parent: Path,
    name: str = "strings",
    namespace: str = "acme",
    language: str = "bash",
    version: str = "1.0.0",
    load_script: str = LOAD_SCRIPT,
) -> Path:
    workdir = Path(tempfile.mkdtemp(dir=parent, prefix="libsrc-"))
    package_dir = workdir / name
    (package_dir / "src").mkdir(parents=True)
    (package_dir / "meta.toml").write_text(
        LIBRARY_MANIFEST.format(
            namespace=namespace, name=name, version=version, language=language
        )
    )
    (package_dir / "src" / "load.sh").write_text(load_script)
    return pack_package(package_dir, parent / "archives")[0]


def build_consumer(parent: Path, body: str, language: str = "bash") -> Path:
    """A command package whose script sources a library."""
    workdir = Path(tempfile.mkdtemp(dir=parent, prefix="appsrc-"))
    package_dir = workdir / "consumer"
    (package_dir / "src").mkdir(parents=True)
    (package_dir / "meta.toml").write_text(
        f"""\
[package]
namespace = "acme"
name = "consumer"
version = "1.0.0"
language = "{language}"
description = "Sources a library"

[platforms]
os = ["linux", "macos"]
"""
    )
    (package_dir / "src" / "main.sh").write_text(body)
    return pack_package(package_dir, parent / "archives")[0]


def lockfile(home: Path) -> dict:
    return json.loads((home / "installed.lock").read_text())


def shim_path(home: Path, command: str) -> Path:
    return home / "bin" / (f"{command}.cmd" if os.name == "nt" else command)


def test_installing_a_library_stages_a_wrapper_and_no_shims(home, tmp_path):
    result = runner.invoke(app, ["install", "-f", str(build_library(tmp_path)), "-y"])
    assert result.exit_code == 0, result.output

    wrapper = libraries.library_dir(home, "acme", "strings") / "load.sh"
    assert wrapper.is_file()
    # The version-less path points at the versioned tree.
    assert str(home / "pkgs" / "acme" / "strings" / "1.0.0") in wrapper.read_text()
    # The loader lands beside it, so a library install is enough to use one.
    assert libraries.loader_path(home).is_file()

    [entry] = lockfile(home)["packages"]
    assert entry["library"] == "src/load.sh"
    assert entry["commands"] == []
    assert entry["shims"] == []
    # A library provides nothing runnable — nothing goes into bin/.
    assert not any((home / "bin").iterdir())


def test_upgrading_a_library_repoints_the_wrapper(home, tmp_path):
    runner.invoke(app, ["install", "-f", str(build_library(tmp_path)), "-y"])
    runner.invoke(
        app, ["install", "-f", str(build_library(tmp_path, version="2.0.0")), "-y"]
    )

    wrapper = libraries.library_dir(home, "acme", "strings") / "load.sh"
    assert str(home / "pkgs" / "acme" / "strings" / "2.0.0") in wrapper.read_text()
    assert "1.0.0" not in wrapper.read_text()


def test_uninstalling_a_library_removes_the_staged_wrapper(home, tmp_path):
    runner.invoke(app, ["install", "-f", str(build_library(tmp_path)), "-y"])
    assert libraries.library_dir(home, "acme", "strings").is_dir()

    result = runner.invoke(app, ["uninstall", "acme/strings", "-y"])
    assert result.exit_code == 0, result.output

    assert not libraries.library_dir(home, "acme", "strings").exists()
    # The namespace level is pruned when it empties, as pkgs/ already is.
    assert not (libraries.lib_root(home) / "acme").exists()
    assert libraries.loader_path(home).is_file()  # the loader is not a package


@pytest.mark.skipif(os.name == "nt", reason="POSIX shim sources the loader")
def test_a_shell_command_can_load_a_library_through_its_shim(home, tmp_path):
    # The whole point of the source-wrapper shim (D57): a bash command finds
    # scr_load in scope without doing anything to arrange it.
    runner.invoke(app, ["install", "-f", str(build_library(tmp_path)), "-y"])
    consumer = build_consumer(
        tmp_path, 'scr_load acme/strings\nscr_shout "hello"\n'
    )
    result = runner.invoke(app, ["install", "-f", str(consumer), "-y"])
    assert result.exit_code == 0, result.output

    completed = subprocess.run(
        [str(shim_path(home, "acme.consumer.consumer"))], capture_output=True, text=True
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "SHOUT:hello"


@pytest.mark.skipif(os.name == "nt", reason="POSIX shim sources the loader")
def test_a_shell_command_still_receives_its_arguments(home, tmp_path):
    # Sourcing rather than exec'ing must not cost the script its "$@".
    consumer = build_consumer(tmp_path, 'echo "args:$*"\n')
    runner.invoke(app, ["install", "-f", str(consumer), "-y"])

    completed = subprocess.run(
        [str(shim_path(home, "acme.consumer.consumer")), "one", "two"],
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "args:one two"


@pytest.mark.skipif(os.name == "nt", reason="POSIX shim sources the loader")
def test_a_shell_command_exits_with_its_script_status(home, tmp_path):
    consumer = build_consumer(tmp_path, "exit 7\n")
    runner.invoke(app, ["install", "-f", str(consumer), "-y"])

    completed = subprocess.run(
        [str(shim_path(home, "acme.consumer.consumer"))], capture_output=True
    )

    assert completed.returncode == 7


@pytest.mark.skipif(os.name == "nt", reason="POSIX shim sources the loader")
def test_a_shell_command_runs_cleanly_with_no_library_in_sight(home, tmp_path):
    # The shim sources the loader whether or not this package has anything to
    # do with libraries, so installing a plain bash command must lay the loader
    # down too — otherwise every run would print a "no such file" to stderr.
    consumer = build_consumer(tmp_path, 'echo "plain"\n')
    runner.invoke(app, ["install", "-f", str(consumer), "-y"])

    assert libraries.loader_path(home).is_file()

    completed = subprocess.run(
        [str(shim_path(home, "acme.consumer.consumer"))], capture_output=True, text=True
    )

    assert completed.returncode == 0
    assert completed.stdout.strip() == "plain"
    assert completed.stderr == ""


@pytest.mark.skipif(os.name == "nt", reason="POSIX shim sources the loader")
def test_a_deleted_loader_does_not_break_installed_commands(home, tmp_path):
    # Defence in depth: the guard in the shim means a user who clears out
    # ~/.scripticus/lib still gets a working command, just without scr_load.
    consumer = build_consumer(tmp_path, 'echo "plain"\n')
    runner.invoke(app, ["install", "-f", str(consumer), "-y"])
    libraries.loader_path(home).unlink()

    completed = subprocess.run(
        [str(shim_path(home, "acme.consumer.consumer"))], capture_output=True, text=True
    )

    assert completed.returncode == 0
    assert completed.stdout.strip() == "plain"
    assert completed.stderr == ""


@pytest.mark.skipif(os.name == "nt", reason="POSIX shim")
def test_a_python_command_keeps_the_exec_shim(home, tmp_path):
    # Nothing outside the shell family can source anything, so those shims are
    # left exactly as they were.
    from scripticus.scaffold import scaffold_package

    workdir = Path(tempfile.mkdtemp(dir=tmp_path, prefix="pysrc-"))
    scaffold_package("python", "py-tool", "acme", workdir)
    archive = pack_package(workdir / "py-tool", tmp_path / "archives")[0]
    runner.invoke(app, ["install", "-f", str(archive), "-y"])

    text = shim_path(home, "acme.py-tool.py-tool").read_text()
    assert text.startswith("#!/bin/sh\nexec python3 ")
    assert "scr_load" not in text


class FakeStaged:
    """Stands in for remote_install.Staged: only the manifest is read."""

    def __init__(self, manifest):
        self.manifest = manifest


def staged_package(tmp_path, name, language, library=False, dependencies=None):
    from scripticus_schema.manifest import load_manifest

    package_dir = tmp_path / f"staged-{name}"
    (package_dir / "src").mkdir(parents=True)
    marker = "\n[library]\n" if library else ""
    depends = "".join(
        f'"{target}" = "{spec}"\n' for target, spec in (dependencies or {}).items()
    )
    (package_dir / "meta.toml").write_text(
        f"""\
[package]
namespace = "acme"
name = "{name}"
version = "1.0.0"
language = "{language}"

[platforms]
os = ["linux", "macos"]
{marker}
[dependencies.packages]
{depends}
"""
    )
    entry = "load.sh" if library else "main.sh"
    (package_dir / "src" / entry).write_text("# code\n")
    return FakeStaged(load_manifest(package_dir))


def test_the_client_rejects_a_library_it_cannot_source(tmp_path):
    # The resolver enforced this server-side; the client re-checks the
    # manifests it actually downloaded rather than taking the index's word
    # for it (D57), the same instinct as re-hashing the blob.
    from scripticus.remote_install import RemoteInstallError, verify_library_languages

    staged = [
        staged_package(tmp_path, "consumer", "sh", dependencies={"acme/strings": "^1"}),
        staged_package(tmp_path, "strings", "bash", library=True),
    ]

    with pytest.raises(RemoteInstallError, match="cannot source library"):
        verify_library_languages(staged, {"packages": []})


def test_the_client_accepts_a_portable_library(tmp_path):
    from scripticus.remote_install import verify_library_languages

    staged = [
        staged_package(tmp_path, "consumer", "bash", dependencies={"acme/strings": "^1"}),
        staged_package(tmp_path, "strings", "sh", library=True),
    ]

    verify_library_languages(staged, {"packages": []})  # does not raise


def test_the_client_checks_against_an_already_installed_library(tmp_path):
    # The library may be already satisfied and so never staged; its language
    # comes from the lockfile instead.
    from scripticus.remote_install import RemoteInstallError, verify_library_languages

    staged = [
        staged_package(tmp_path, "consumer", "sh", dependencies={"acme/strings": "^1"})
    ]
    lock = {
        "packages": [
            {
                "namespace": "acme",
                "name": "strings",
                "language": "bash",
                "library": "src/load.sh",
            }
        ]
    }

    with pytest.raises(RemoteInstallError, match="cannot source library"):
        verify_library_languages(staged, lock)


def seed_lock(home: Path, packages: list[dict]) -> None:
    home.mkdir(parents=True, exist_ok=True)
    (home / "installed.lock").write_text(json.dumps({"packages": packages}))


def test_uninstall_reports_an_orphaned_library_without_removing_it(home, tmp_path):
    # Install a real library so there is something on disk, then rewrite the
    # lockfile to make it look like a dependency of a package being removed.
    runner.invoke(app, ["install", "-f", str(build_library(tmp_path)), "-y"])
    lock = lockfile(home)
    library_entry = lock["packages"][0]
    library_entry["direct"] = False
    lock["packages"].append(
        {
            "namespace": "acme",
            "name": "consumer",
            "version": "1.0.0",
            "language": "bash",
            "content_hash": "sha256:x",
            "commands": [],
            "snippets": {},
            "library": None,
            "shims": [],
            "direct": True,
            "provenance": {"type": "local", "source": "x"},
            "dependencies": {"acme/strings": "^1"},
        }
    )
    seed_lock(home, lock["packages"])

    result = runner.invoke(app, ["uninstall", "acme/consumer", "-y"])

    assert result.exit_code == 0, result.output
    assert "no installed package now needs" in result.output
    assert "acme/strings" in result.output
    # Advisory only — the library is still installed and still loadable.
    assert libraries.library_dir(home, "acme", "strings").is_dir()
    assert [e["name"] for e in lockfile(home)["packages"]] == ["strings"]


def test_uninstalling_a_library_warns_that_sourcing_will_break(home, tmp_path):
    runner.invoke(app, ["install", "-f", str(build_library(tmp_path)), "-y"])

    result = runner.invoke(app, ["uninstall", "acme/strings", "-y"])

    assert "scr_load acme/strings" in result.output


def test_list_tags_an_installed_library(home, tmp_path):
    runner.invoke(app, ["install", "-f", str(build_library(tmp_path)), "-y"])

    result = runner.invoke(app, ["list", "--installed"])

    assert result.exit_code == 0, result.output
    assert "library" in result.output


def test_orphaned_libraries_are_reported_not_removed():
    # A library that arrived as a dependency and is now needed by nothing is
    # worth mentioning — but uninstall never removes a package the user did not
    # name, which is how orphaned command packages already behave.
    lock = {
        "packages": [
            {
                "namespace": "acme",
                "name": "strings",
                "library": "src/load.sh",
                "direct": False,
                "dependencies": {},
            },
            {
                "namespace": "acme",
                "name": "keeper",
                "library": "src/load.sh",
                "direct": False,
                "dependencies": {},
            },
            {
                "namespace": "acme",
                "name": "app",
                "direct": True,
                "dependencies": {"acme/keeper": "^1"},
            },
        ]
    }

    orphans = libraries.orphaned_libraries(lock)

    assert [entry["name"] for entry in orphans] == ["strings"]


def test_a_directly_installed_library_is_never_an_orphan():
    # The user asked for it; it stays until they say otherwise.
    lock = {
        "packages": [
            {
                "namespace": "acme",
                "name": "strings",
                "library": "src/load.sh",
                "direct": True,
                "dependencies": {},
            }
        ]
    }

    assert libraries.orphaned_libraries(lock) == []
