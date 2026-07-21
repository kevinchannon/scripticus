"""Tool-installation runner (D44): PATH presence, command building, and the
operator-configured install command."""

import subprocess

import pytest

from scripticus.config import Tools
from scripticus.tools import (
    ToolError,
    install_command,
    install_missing_required,
    missing_on_path,
)


# --- Presence on PATH ------------------------------------------------------


def test_missing_on_path_filters_and_preserves_order(monkeypatch):
    present = {"git", "curl"}
    monkeypatch.setattr(
        "shutil.which", lambda name: "/usr/bin/x" if name in present else None
    )
    assert missing_on_path(["git", "fzf", "curl", "ripgrep"]) == ["fzf", "ripgrep"]


def test_missing_on_path_dedupes(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: None)
    assert missing_on_path(["fzf", "fzf", "bat"]) == ["fzf", "bat"]


def test_missing_on_path_all_present(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/x")
    assert missing_on_path(["git", "curl"]) == []


# --- Building the install command ------------------------------------------


def test_install_command_substitutes_placeholder():
    assert (
        install_command("apt-get install -y {packages}", None, ["git", "fzf"])
        == "apt-get install -y git fzf"
    )


def test_install_command_appends_when_no_placeholder():
    assert install_command("brew install", None, ["git", "fzf"]) == "brew install git fzf"


def test_install_command_prepends_escalate():
    assert (
        install_command("apt-get install -y {packages}", "sudo", ["git"])
        == "sudo apt-get install -y git"
    )


def test_install_command_shell_quotes_names():
    # Names are already charset-validated at manifest parse, but quoting is
    # belt-and-braces: nothing a name contains can break out of an argument.
    assert install_command("install {packages}", None, ["a.b+c-d"]) == "install a.b+c-d"


# --- Running the installer -------------------------------------------------


def test_install_missing_required_no_missing_is_a_noop(monkeypatch):
    called = False

    def fail(*args, **kwargs):  # pragma: no cover - must not run
        nonlocal called
        called = True

    monkeypatch.setattr(subprocess, "run", fail)
    install_missing_required([], Tools(install=None, escalate=None))
    assert not called


def test_install_missing_required_refuses_without_installer():
    with pytest.raises(ToolError, match="missing required system tools: git, fzf"):
        install_missing_required(["git", "fzf"], Tools(install=None, escalate=None))


def test_install_missing_required_refusal_hints_skip_tools():
    with pytest.raises(ToolError, match="--skip-tools"):
        install_missing_required(["git"], Tools(install=None, escalate=None))


def test_install_missing_required_runs_configured_command(monkeypatch):
    seen = {}

    def fake_run(argv, *args, **kwargs):
        seen["argv"] = argv
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr("os.name", "posix")
    install_missing_required(
        ["git", "fzf"], Tools(install="apt-get install -y {packages}", escalate="sudo")
    )
    assert seen["argv"] == ["bash", "-lc", "sudo apt-get install -y git fzf"]


def test_install_missing_required_windows_shell(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        subprocess, "run", lambda argv, *a, **k: seen.update(argv=argv) or subprocess.CompletedProcess(argv, 0)
    )
    monkeypatch.setattr("os.name", "nt")
    install_missing_required(["git"], Tools(install="choco install {packages}", escalate=None))
    assert seen["argv"] == ["cmd", "/c", "choco install git"]


def test_install_missing_required_raises_on_nonzero_exit(monkeypatch):
    monkeypatch.setattr(
        subprocess, "run", lambda argv, *a, **k: subprocess.CompletedProcess(argv, 3)
    )
    with pytest.raises(ToolError, match="exit 3"):
        install_missing_required(["git"], Tools(install="apt-get install {packages}"))
