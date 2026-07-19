"""Local package removal (`scripticus uninstall`).

Operates entirely against ``installed.lock`` — no server round-trip (D10).
A package's lock entry lists only the command shims it currently owns
(last-install-wins, D11), so uninstalling never removes a shim that another
package has since taken over.

Removing a shim owner can orphan a command that other installed packages
still provide (D28). Providers are discovered by re-reading the installed
manifests under ``pkgs/`` rather than from the lockfile: the manifest is the
authoritative source (D21) and this works for packages installed before
replacement discovery existed.
"""

import shutil
from dataclasses import dataclass
from pathlib import Path

from scripticus.install import _find_entry, _shim_path, _write_shim, write_lockfile
from scripticus.manifest import ManifestError, commands_of, load_manifest


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


@dataclass
class Candidate:
    """An installed package that provides a command being orphaned."""

    namespace: str
    name: str
    version: str
    language: str
    script: str  # package-relative path the command maps to

    @property
    def package_id(self) -> str:
        return f"{self.namespace}/{self.name}"


def find_replacements(
    removed: dict, lock: dict, home: Path
) -> dict[str, list[Candidate]]:
    """Map each command owned by ``removed`` to other installed packages
    whose manifests also provide it. Commands nobody else provides are
    absent from the result.
    """
    replacements: dict[str, list[Candidate]] = {}
    owned = set(removed["commands"])
    for entry in lock["packages"]:
        if entry is removed:
            continue
        package_dir = home / "pkgs" / entry["namespace"] / entry["name"] / entry["version"]
        try:
            manifest = load_manifest(package_dir)
        except ManifestError:
            continue  # a damaged tree shouldn't block the uninstall
        for command, script in commands_of(manifest).items():
            if command in owned:
                replacements.setdefault(command, []).append(
                    Candidate(
                        namespace=entry["namespace"],
                        name=entry["name"],
                        version=entry["version"],
                        language=manifest["package"]["language"],
                        script=script,
                    )
                )
    return replacements


def install_replacement(candidate: Candidate, command: str, lock: dict, home: Path) -> None:
    """Point ``command``'s shim at ``candidate`` and record the ownership,
    taking it away from any current owner.

    This is the re-point primitive shared with `scripticus use`.
    """
    bin_dir = home / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    script = home / "pkgs" / candidate.namespace / candidate.name / candidate.version / candidate.script
    _write_shim(bin_dir, command, script, candidate.language)

    entry = _find_entry(lock, candidate.namespace, candidate.name)
    for other in lock["packages"]:
        if other is not entry and command in other["commands"]:
            other["commands"].remove(command)
    if entry is not None and command not in entry["commands"]:
        entry["commands"] = sorted([*entry["commands"], command])
    write_lockfile(home, lock)


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
