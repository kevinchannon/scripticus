"""Tool-installation runner (D44): PATH presence, command building, and the
operator-configured install command — plus the package-manager suggestion
table and its detection (D64)."""

import os
import subprocess

import pytest

from scripticus.config import Tools
from scripticus.tools import (
    _MANAGERS as MANAGERS,
    ToolError,
    detect_package_manager,
    escalation,
    install_command,
    install_missing_required,
    missing_on_path,
    suggested_tools,
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


# --- Package-manager detection (D64) ---------------------------------------


def _present(monkeypatch, *programs):
    """Only `programs` are on PATH."""
    found = set(programs)
    monkeypatch.setattr(
        "shutil.which", lambda name: f"/usr/bin/{name}" if name in found else None
    )


def test_detects_the_platforms_package_manager(monkeypatch):
    _present(monkeypatch, "apt-get", "sudo")
    manager = detect_package_manager("linux")
    assert manager is not None and manager.program == "apt-get"
    assert suggested_tools(manager) == Tools(
        install="apt-get install -y {packages}", escalate="sudo"
    )


def test_first_manager_on_path_wins(monkeypatch):
    # A machine with both: the table's order is the tie-break, not PATH order.
    _present(monkeypatch, "dnf", "apt-get", "sudo")
    assert detect_package_manager("linux").program == "apt-get"

    _present(monkeypatch, "dnf", "zypper", "sudo")
    assert detect_package_manager("linux").program == "dnf"


def test_no_known_manager_detects_nothing(monkeypatch):
    _present(monkeypatch, "sudo")
    assert detect_package_manager("linux") is None


def test_managers_are_platform_scoped(monkeypatch):
    # brew on a Linux box (it exists) must not shadow the native manager, and
    # a Linux manager must never be suggested on macOS.
    _present(monkeypatch, "brew", "apt-get", "sudo")
    assert detect_package_manager("linux").program == "apt-get"
    assert detect_package_manager("darwin").program == "brew"


def test_platform_key_normalises_versioned_platforms(monkeypatch):
    _present(monkeypatch, "apt-get", "sudo")
    assert detect_package_manager("linux2").program == "apt-get"


def test_homebrew_is_never_escalated(monkeypatch):
    # Homebrew refuses to run under sudo, so suggesting it would be a broken
    # command even though sudo is available.
    _present(monkeypatch, "brew", "sudo")
    assert suggested_tools(detect_package_manager("darwin")).escalate is None


def test_escalation_prefers_sudo_then_doas(monkeypatch):
    monkeypatch.setattr(os, "geteuid", lambda: 1000, raising=False)

    _present(monkeypatch, "sudo", "doas")
    assert escalation() == "sudo"

    _present(monkeypatch, "doas")
    assert escalation() == "doas"

    _present(monkeypatch)
    assert escalation() is None


@pytest.mark.skipif(os.name == "nt", reason="euid is a POSIX notion")
def test_root_needs_no_escalation(monkeypatch):
    # The Alpine-in-a-container case: sudo may not even be installed.
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    _present(monkeypatch, "apk", "sudo")
    assert suggested_tools(detect_package_manager("linux")).escalate is None


def test_suggested_commands_are_non_interactive():
    """An install command that stops to ask would hang: `install` runs it with
    inherited stdio and no way to answer on the user's behalf."""
    for managers in MANAGERS.values():
        for manager in managers:
            assert "{packages}" in manager.install
            built = install_command(manager.install, None, ["jq", "curl"])
            assert built.endswith("jq curl") or " jq curl " in built
