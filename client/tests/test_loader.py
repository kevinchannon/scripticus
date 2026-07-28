"""The `scr_load` loader (D57), exercised by really running it.

The loader is POSIX sh sourced into someone else's shell, so the only test worth
having runs it in a real shell — under `sh` *and* `bash`, since serving both is
the whole point of the shell-only scope. Everything here builds a `lib/` tree by
hand (the shapes `libraries.py` generates) and asks a shell what happened.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

from scripticus import libraries

SHELLS = ["sh", "bash"]


@pytest.fixture(params=SHELLS)
def shell(request):
    path = shutil.which(request.param)
    if path is None:
        pytest.skip(f"{request.param} is not installed")
    return path


def make_library(home: Path, package_id: str, load_body: str, siblings=None) -> None:
    """Install a fake library: a versioned tree under pkgs/ plus the generated
    wrapper under lib/, exactly as `stage_library` lays it out.
    """
    namespace, name = package_id.split("/")
    install_dir = home / "pkgs" / namespace / name / "1.0.0"
    (install_dir / "src").mkdir(parents=True, exist_ok=True)
    (install_dir / "src" / "load.sh").write_text(load_body)
    for filename, body in (siblings or {}).items():
        (install_dir / "src" / filename).write_text(body)
    libraries.stage_library(home, namespace, name, install_dir, "src/load.sh")


def run(shell: str, home: Path, script: str) -> subprocess.CompletedProcess:
    """Run a consumer script with the loader sourced, as a shim would."""
    return subprocess.run(
        [shell, "-c", f'. "{libraries.loader_path(home)}"\n{script}'],
        capture_output=True,
        text=True,
        env={"SCRIPTICUS_LIB": str(libraries.lib_root(home)), "PATH": "/usr/bin:/bin"},
    )


def test_loads_a_library_and_its_functions(shell, tmp_path):
    libraries.install_loader(tmp_path)
    make_library(tmp_path, "acme/strings", 'scr_upper() { echo "UPPER:$1"; }\n')

    result = run(shell, tmp_path, 'scr_load acme/strings\nscr_upper hi')

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "UPPER:hi"


def test_a_library_can_source_its_own_siblings(shell, tmp_path):
    # POSIX sh gives a sourced file no way to find its own directory, so
    # SCR_LIB_DIR is the only way a multi-file library can work at all.
    libraries.install_loader(tmp_path)
    make_library(
        tmp_path,
        "acme/strings",
        '. "$SCR_LIB_DIR/src/helpers.sh"\n',
        siblings={"helpers.sh": 'scr_helper() { echo "helped"; }\n'},
    )

    result = run(shell, tmp_path, "scr_load acme/strings\nscr_helper")

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "helped"


def test_loading_is_transitive(shell, tmp_path):
    libraries.install_loader(tmp_path)
    make_library(tmp_path, "acme/base", 'scr_base() { echo "base"; }\n')
    make_library(
        tmp_path,
        "acme/strings",
        'scr_load acme/base\nscr_strings() { scr_base; echo "strings"; }\n',
    )

    result = run(shell, tmp_path, "scr_load acme/strings\nscr_strings")

    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["base", "strings"]


def test_a_repeat_load_is_a_no_op(shell, tmp_path):
    # The include guard, which is what makes a diamond free: loading twice must
    # not re-run the library's top-level code.
    libraries.install_loader(tmp_path)
    make_library(tmp_path, "acme/strings", 'echo "loading"\n')

    result = run(
        shell, tmp_path, "scr_load acme/strings\nscr_load acme/strings\necho done"
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["loading", "done"]


def test_a_diamond_loads_the_shared_library_once(shell, tmp_path):
    libraries.install_loader(tmp_path)
    make_library(tmp_path, "acme/base", 'echo "base loaded"\n')
    make_library(tmp_path, "acme/left", "scr_load acme/base\n")
    make_library(tmp_path, "acme/right", "scr_load acme/base\n")

    result = run(shell, tmp_path, "scr_load acme/left\nscr_load acme/right\necho done")

    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["base", "loaded", "done"]


def test_a_cycle_terminates(shell, tmp_path):
    # The guard is set before sourcing precisely so this returns instead of
    # recursing until the shell dies.
    libraries.install_loader(tmp_path)
    make_library(tmp_path, "acme/ping", "scr_load acme/pong\n")
    make_library(tmp_path, "acme/pong", "scr_load acme/ping\n")

    result = run(shell, tmp_path, "scr_load acme/ping\necho done")

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "done"


def test_a_nested_load_does_not_clobber_the_outer_lib_dir(shell, tmp_path):
    # The subtle one: a library that loads another library and *then* sources a
    # sibling must still see its own directory.
    libraries.install_loader(tmp_path)
    make_library(tmp_path, "acme/base", "scr_load_noop() { :; }\n")
    make_library(
        tmp_path,
        "acme/strings",
        'scr_load acme/base\n. "$SCR_LIB_DIR/src/helpers.sh"\n',
        siblings={"helpers.sh": 'scr_helper() { echo "helped"; }\n'},
    )

    result = run(shell, tmp_path, "scr_load acme/strings\nscr_helper")

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "helped"


def test_a_missing_library_returns_non_zero_without_aborting(shell, tmp_path):
    # Soft-fail (D57): the caller decides whether a missing library is fatal,
    # so the script must still be running afterwards.
    libraries.install_loader(tmp_path)

    result = run(
        shell,
        tmp_path,
        'if scr_load acme/absent; then echo "loaded"; else echo "handled"; fi',
    )

    assert result.returncode == 0
    assert result.stdout.strip() == "handled"
    assert "no library 'acme/absent' is installed" in result.stderr


def test_a_bare_reference_is_rejected(shell, tmp_path):
    # v1 references are fully namespaced (D46); a bare name is a mistake worth
    # naming rather than a lookup across namespaces.
    libraries.install_loader(tmp_path)

    result = run(shell, tmp_path, "scr_load strings; echo \"rc=$?\"")

    assert "not fully namespaced" in result.stderr
    assert result.stdout.strip() == "rc=2"


def test_the_loader_reports_a_failing_library(shell, tmp_path):
    # A library whose load script fails must not look like a success.
    libraries.install_loader(tmp_path)
    make_library(tmp_path, "acme/broken", "return 3\n")

    result = run(shell, tmp_path, 'scr_load acme/broken; echo "rc=$?"')

    assert result.stdout.strip() == "rc=3"
