"""One-time post-install bootstrap (`scripticus init`, D39; `install`, D63).

pip cannot edit a shell profile, so the "bin dir added to PATH once at
install time" step D11's shim scheme assumes needs a command. POSIX gets
one guarded line appended to a single profile file chosen from ``$SHELL``
(zsh → ``~/.zshrc``, bash → ``~/.bashrc``, anything else →
``~/.profile``); Windows gets the bin directory appended to the per-user
``Path`` registry value. Idempotent throughout: nothing is written when
the bin directory is already on the live PATH or already in the target.

Because it is idempotent, it need not be a step the user remembers: the whole
job is ``bootstrap()``, which `install` runs at its own commit point (D63) so
a shim is never written into a directory the shell cannot find. `init` remains
for setting a machine up before installing anything.
"""

import os
from dataclasses import dataclass
from pathlib import Path

from scripticus import libraries

_PROFILES = {"zsh": ".zshrc", "bash": ".bashrc"}


def ensure_skeleton(home: Path) -> bool:
    """Create the client state skeleton; True if anything was created.

    Also lays down (or refreshes) the ``scr_load`` loader, so a script can opt
    into libraries before any library is installed — and so an upgraded client
    replaces an older loader (D57).
    """
    bin_dir = home / "bin"
    created = not bin_dir.is_dir()
    bin_dir.mkdir(parents=True, exist_ok=True)
    libraries.install_loader(home)
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


def lib_line(bin_dir: Path) -> str:
    """The ``SCRIPTICUS_LIB`` export (D57).

    A user's own ad-hoc script opts into ``scr_load`` by sourcing
    ``$SCRIPTICUS_LIB/scr_load.sh``, which needs the variable exported globally
    rather than only inside the shims Scripticus writes.

    Deliberately a *second* line rather than an addition to the PATH one: a user
    who ran `init` before libraries existed already has the PATH line, so
    folding the two together would leave them silently without the export.
    """
    return f'export SCRIPTICUS_LIB="{bin_dir.parent / "lib"}"  # added by scripticus init'


def _ensure_profile_path(bin_dir: Path, environ) -> tuple[bool, str]:
    profile = profile_path(environ)
    text = profile.read_text() if profile.is_file() else ""
    # Each line is guarded on its own marker, so an upgrade adds only what is
    # missing and re-running adds nothing.
    missing = [
        line
        for marker, line in (
            (str(bin_dir), path_line(bin_dir)),
            ("SCRIPTICUS_LIB", lib_line(bin_dir)),
        )
        if marker not in text
    ]
    if not missing:
        return False, str(profile)
    separator = "" if not text or text.endswith("\n") else "\n"
    with profile.open("a") as file:
        file.write(separator + "".join(f"{line}\n" for line in missing))
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


@dataclass
class Bootstrap:
    """What a bootstrap run had to do. Every flag is False on a machine that
    was already set up, which is what lets callers stay quiet about it.
    """

    created_home: bool
    path_changed: bool
    path_location: str
    on_live_path: bool


def bootstrap(home: Path, environ=os.environ) -> Bootstrap:
    """The whole of `init`'s job, idempotently (D63).

    A bin directory already on the live PATH suppresses the persistent edit
    entirely — the manual setups D39 respects are exactly the ones that need
    nothing done.
    """
    bin_dir = home / "bin"
    created = ensure_skeleton(home)
    if on_path(bin_dir, environ):
        return Bootstrap(created, path_changed=False, path_location="", on_live_path=True)
    changed, where = ensure_persistent_path(bin_dir, environ)
    return Bootstrap(created, changed, where, on_live_path=False)
