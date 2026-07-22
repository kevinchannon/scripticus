"""Integration tests for multi-file packages: a package whose entrypoint
calls a sibling helper script (or reads a sibling data file).

The shim invokes the real script by its absolute path in the install tree
(no symlink, no ``cd``), and the whole package tree is copied verbatim
(``shutil.copytree``), so a helper keeps its position next to the
entrypoint. Scripts that locate siblings *relative to their own file*
(``$BASH_SOURCE`` / ``__file__`` / ``$PSScriptRoot``) therefore work
regardless of the caller's working directory. Scripts that resolve siblings
relative to the *current working directory* (``./helper.sh``) do not — that
is a script-authoring constraint the authoring docs warn about, and the
characterization tests below pin the actual behaviour.

These tests install a real packed archive and *run* the installed shim in a
subprocess from an unrelated working directory, so they exercise the real
interpreter dispatch, not a mock.
"""

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

from scripticus.cli import app
from scripticus.install import current_os
from scripticus.pack import pack_package

runner = CliRunner()

# language -> interpreter that must be on PATH for its shim to run.
INTERPRETERS = {"bash": "bash", "python": "python3", "powershell": "pwsh"}


@pytest.fixture
def home(tmp_path, monkeypatch):
    home_dir = tmp_path / "scripticus-home"
    monkeypatch.setenv("SCRIPTICUS_HOME", str(home_dir))
    monkeypatch.chdir(tmp_path)
    return home_dir


def shim_path(home: Path, command: str) -> Path:
    return home / "bin" / (f"{command}.cmd" if os.name == "nt" else command)


def build_pkg(
    parent: Path,
    language: str,
    files: dict[str, str],
    commands: dict[str, str],
    *,
    name: str = "multi",
    namespace: str = "acme",
    version: str = "0.1.0",
) -> Path:
    """Build and pack a package from an explicit file map, returning the
    first archive (the POSIX ``.tar.gz`` on non-Windows platforms).

    ``platforms.os`` is pinned to the current OS so the package installs on
    whatever machine runs the test (the scaffold defaults would refuse a
    PowerShell package off Windows).
    """
    pkg = Path(tempfile.mkdtemp(dir=parent, prefix="pkgsrc-")) / name
    (pkg / "src").mkdir(parents=True)

    command_table = "\n".join(f'{cmd} = "{script}"' for cmd, script in commands.items())
    (pkg / "meta.toml").write_text(
        f'[package]\n'
        f'namespace = "{namespace}"\n'
        f'name = "{name}"\n'
        f'version = "{version}"\n'
        f'language = "{language}"\n'
        f'description = "multifile integration test"\n\n'
        f'[platforms]\n'
        f'os = ["{current_os()}"]\n\n'
        f'[commands]\n{command_table}\n'
    )
    for relative, content in files.items():
        path = pkg / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)

    return pack_package(pkg, parent / "archives")[0]


def run_shim(home: Path, command: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(shim_path(home, command))], cwd=str(cwd), capture_output=True, text=True
    )


# A main entrypoint that resolves a helper *and* a data file relative to its
# own location, then prints what each yields. Correct install + file-relative
# resolution produces both marker lines regardless of the caller's cwd.
FILE_RELATIVE = {
    "python": {
        "src/main.py": (
            "import sys\n"
            "from pathlib import Path\n"
            "here = Path(__file__).resolve().parent\n"
            "sys.path.insert(0, str(here))\n"
            "import helper\n"
            "print(helper.greeting())\n"
            "print((here / 'data.txt').read_text().strip())\n"
        ),
        "src/helper.py": "def greeting():\n    return 'helper-says-hi'\n",
        "src/data.txt": "DATA-OK\n",
    },
    "bash": {
        "src/main.sh": (
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            'here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"\n'
            'source "$here/helper.sh"\n'
            "greeting\n"
            'cat "$here/data.txt"\n'
        ),
        "src/helper.sh": "greeting() { echo 'helper-says-hi'; }\n",
        "src/data.txt": "DATA-OK\n",
    },
    "powershell": {
        "src/main.ps1": (
            "$ErrorActionPreference = 'Stop'\n"
            ". (Join-Path $PSScriptRoot 'helper.ps1')\n"
            "Write-Output (Get-Greeting)\n"
            "Write-Output ((Get-Content -Raw (Join-Path $PSScriptRoot 'data.txt')).Trim())\n"
        ),
        "src/helper.ps1": "function Get-Greeting { 'helper-says-hi' }\n",
        "src/data.txt": "DATA-OK\n",
    },
}
ENTRYPOINTS = {"python": "src/main.py", "bash": "src/main.sh", "powershell": "src/main.ps1"}


@pytest.mark.skipif(os.name == "nt", reason="POSIX shim execution")
@pytest.mark.parametrize("language", ["python", "bash", "powershell"])
def test_file_relative_helper_and_data_resolve(home, tmp_path, language):
    """A package's entrypoint can call a sibling helper and read a sibling
    data file when it resolves them relative to its own file — for every
    supported language — and it works from an unrelated working directory."""
    interpreter = INTERPRETERS[language]
    if shutil.which(interpreter) is None:
        pytest.skip(f"{interpreter} not installed")

    archive = build_pkg(
        tmp_path, language, FILE_RELATIVE[language], {"multi": ENTRYPOINTS[language]}
    )
    result = runner.invoke(app, ["install", "-f", str(archive), "-y"])
    assert result.exit_code == 0, result.output

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    for tier in ("multi", "acme.multi", "acme.multi.multi"):
        completed = run_shim(home, tier, cwd=elsewhere)
        assert completed.returncode == 0, completed.stderr
        assert "helper-says-hi" in completed.stdout
        assert "DATA-OK" in completed.stdout


@pytest.mark.skipif(os.name == "nt", reason="POSIX shim execution")
def test_multiple_commands_share_one_helper(home, tmp_path):
    """Two commands in the same package can each call the shared helper —
    the helper is packed once and both entrypoints resolve it file-relative."""
    if shutil.which("python3") is None:
        pytest.skip("python3 not installed")

    files = {
        "src/helper.py": "def greeting():\n    return 'shared-helper'\n",
        "src/main.py": (
            "import sys\n"
            "from pathlib import Path\n"
            "sys.path.insert(0, str(Path(__file__).resolve().parent))\n"
            "import helper\n"
            "print('main:' + helper.greeting())\n"
        ),
        "src/other.py": (
            "import sys\n"
            "from pathlib import Path\n"
            "sys.path.insert(0, str(Path(__file__).resolve().parent))\n"
            "import helper\n"
            "print('other:' + helper.greeting())\n"
        ),
    }
    archive = build_pkg(
        tmp_path, "python", files, {"multi": "src/main.py", "other": "src/other.py"}
    )
    assert runner.invoke(app, ["install", "-f", str(archive), "-y"]).exit_code == 0

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    main = run_shim(home, "multi", cwd=elsewhere)
    other = run_shim(home, "other", cwd=elsewhere)
    assert main.stdout.strip() == "main:shared-helper", main.stderr
    assert other.stdout.strip() == "other:shared-helper", other.stderr


# --- Characterization: cwd-relative resolution is NOT package-relative ------
#
# These pin the known limitation the authoring docs warn about, so a future
# change to the shim (a `cd`, a PATH tweak) that alters it fails loudly here.


@pytest.mark.skipif(os.name == "nt", reason="POSIX shim execution")
def test_cwd_relative_read_binds_to_invocation_cwd_not_package(home, tmp_path):
    """A cwd-relative open reads from the *caller's* working directory, not
    the package tree — even when a same-named file exists in the package."""
    if shutil.which("python3") is None:
        pytest.skip("python3 not installed")

    files = {
        "src/main.py": "print(open('note.txt').read().strip())\n",
        "src/note.txt": "PKG-NOTE\n",  # packed, but never what a cwd-relative open finds
    }
    archive = build_pkg(tmp_path, "python", files, {"multi": "src/main.py"})
    assert runner.invoke(app, ["install", "-f", str(archive), "-y"]).exit_code == 0

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "note.txt").write_text("CWD-NOTE\n")

    completed = run_shim(home, "multi", cwd=elsewhere)
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "CWD-NOTE"


@pytest.mark.skipif(os.name == "nt", reason="POSIX shim execution")
def test_cwd_relative_read_fails_when_cwd_lacks_the_file(home, tmp_path):
    """The corollary: a cwd-relative open does not fall back to the package
    tree, so running from a directory without the file errors out — this is
    the trap the authoring docs steer authors away from."""
    if shutil.which("python3") is None:
        pytest.skip("python3 not installed")

    files = {
        "src/main.py": "print(open('note.txt').read().strip())\n",
        "src/note.txt": "PKG-NOTE\n",
    }
    archive = build_pkg(tmp_path, "python", files, {"multi": "src/main.py"})
    assert runner.invoke(app, ["install", "-f", str(archive), "-y"]).exit_code == 0

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    completed = run_shim(home, "multi", cwd=elsewhere)
    assert completed.returncode != 0
    assert "FileNotFoundError" in completed.stderr
