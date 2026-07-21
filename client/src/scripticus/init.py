"""One-time post-install bootstrap (`scripticus init`, D39).

pip cannot edit a shell profile, so the "bin dir added to PATH once at
install time" step D11's shim scheme assumes needs a command. POSIX gets
one guarded line appended to a single profile file chosen from ``$SHELL``
(zsh → ``~/.zshrc``, bash → ``~/.bashrc``, anything else →
``~/.profile``); Windows gets the bin directory appended to the per-user
``Path`` registry value. Idempotent throughout: nothing is written when
the bin directory is already on the live PATH or already in the target.
"""

import os
from pathlib import Path

_PROFILES = {"zsh": ".zshrc", "bash": ".bashrc"}


def ensure_skeleton(home: Path) -> bool:
    """Create the client state skeleton; True if anything was created."""
    bin_dir = home / "bin"
    created = not bin_dir.is_dir()
    bin_dir.mkdir(parents=True, exist_ok=True)
    return created


def on_path(bin_dir: Path, environ=os.environ) -> bool:
    """Is ``bin_dir`` already on the live PATH? (Covers manual setups.)"""
    entries = environ.get("PATH", "").split(os.pathsep)
    return any(entry and Path(entry) == bin_dir for entry in entries)


def profile_path(environ=os.environ) -> Path:
    shell = Path(environ.get("SHELL", "")).name
    return Path.home() / _PROFILES.get(shell, ".profile")


def path_line(bin_dir: Path) -> str:
    return f'export PATH="{bin_dir}:$PATH"  # added by scripticus init'


def _ensure_profile_path(bin_dir: Path, environ) -> tuple[bool, str]:
    profile = profile_path(environ)
    text = profile.read_text() if profile.is_file() else ""
    if str(bin_dir) in text:
        return False, str(profile)
    separator = "" if not text or text.endswith("\n") else "\n"
    with profile.open("a") as file:
        file.write(f"{separator}{path_line(bin_dir)}\n")
    return True, str(profile)


def _ensure_windows_path(bin_dir: Path) -> tuple[bool, str]:
    import winreg

    where = "the user PATH (HKCU\\Environment)"
    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_READ | winreg.KEY_WRITE
    ) as key:
        try:
            current, kind = winreg.QueryValueEx(key, "Path")
        except FileNotFoundError:
            current, kind = "", winreg.REG_EXPAND_SZ
        entries = [entry for entry in current.split(";") if entry]
        if any(Path(entry) == bin_dir for entry in entries):
            return False, where
        winreg.SetValueEx(key, "Path", 0, kind, ";".join(entries + [str(bin_dir)]))
    return True, where


def ensure_persistent_path(bin_dir: Path, environ=os.environ) -> tuple[bool, str]:
    """Make ``bin_dir`` part of the persistent PATH; (changed, where)."""
    if os.name == "nt":
        return _ensure_windows_path(bin_dir)
    return _ensure_profile_path(bin_dir, environ)
