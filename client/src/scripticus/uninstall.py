"""Local package removal (`scripticus uninstall`).

Operates entirely against ``installed.lock`` — no server round-trip (D10).
A package's lock entry lists only the command shims it currently owns
(last-install-wins, D11), so uninstalling never removes a shim that another
package has since taken over.
"""

import shutil
from pathlib import Path

from scripticus.install import _shim_path, write_lockfile


class UninstallError(Exception):
    """A package could not be uninstalled."""


def find_installed(spec: str, lock: dict) -> dict:
    """Find the lockfile entry for ``spec`` (``name`` or ``namespace/name``).

    A bare name follows D5: it is a convenience that must resolve
    unambiguously, here against the installed set rather than a search path.
    """
    namespace, _, name = spec.rpartition("/")
    if namespace:
        matches = [
            entry
            for entry in lock["packages"]
            if entry["namespace"] == namespace and entry["name"] == name
        ]
    else:
        matches = [entry for entry in lock["packages"] if entry["name"] == name]

    if not matches:
        raise UninstallError(f"'{spec}' is not installed")
    if len(matches) > 1:
        candidates = ", ".join(sorted(f"{e['namespace']}/{e['name']}" for e in matches))
        raise UninstallError(
            f"'{spec}' matches more than one installed package ({candidates})"
            " — use the namespace/name form"
        )
    return matches[0]


def apply_uninstall(entry: dict, lock: dict, home: Path) -> None:
    """Remove the package's shims and files, then drop it from the lockfile."""
    bin_dir = home / "bin"
    for command in entry["commands"]:
        _shim_path(bin_dir, command).unlink(missing_ok=True)

    package_dir = home / "pkgs" / entry["namespace"] / entry["name"] / entry["version"]
    shutil.rmtree(package_dir, ignore_errors=True)
    for parent in (package_dir.parent, package_dir.parent.parent):
        if parent.is_dir() and not any(parent.iterdir()):
            parent.rmdir()

    lock["packages"].remove(entry)
    write_lockfile(home, lock)
