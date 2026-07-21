"""Local package removal (`scripticus uninstall`).

Operates entirely against ``installed.lock`` — no server round-trip (D10).
A package always owns its fully-qualified shims (D38), but its lock entry's
``shims`` list holds only the convenience shims it currently owns
(last-install-wins, D11), so uninstalling never removes a convenience shim
another package has since taken over.

Removing a convenience-shim owner can orphan a shim whose command other
installed packages still provide (D28; for a namespaced shim, only
same-namespace packages qualify). Providers are discovered by re-reading
the installed manifests under ``pkgs/`` rather than from the lockfile: the
manifest is the authoritative source (D21).
"""

import shutil
from dataclasses import dataclass
from pathlib import Path

from scripticus.install import (
    _find_entry,
    _shim_path,
    _write_delegating_shim,
    _write_shim,
    fq_shim,
    shim_command,
    write_lockfile,
)
from scripticus_schema.manifest import ManifestError, commands_of, load_manifest


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
    """Map each convenience shim owned by ``removed`` to other installed
    packages whose manifests provide its command — same-namespace packages
    only for a namespaced shim (D38). Shims nobody else can serve are
    absent from the result.
    """
    replacements: dict[str, list[Candidate]] = {}
    owned = removed.get("shims", [])
    for entry in lock["packages"]:
        if entry is removed:
            continue
        package_dir = home / "pkgs" / entry["namespace"] / entry["name"] / entry["version"]
        try:
            manifest = load_manifest(package_dir)
        except ManifestError:
            continue  # a damaged tree shouldn't block the uninstall
        provided = commands_of(manifest)
        for shim in owned:
            required_namespace, _, command = shim.rpartition(".")
            if required_namespace and required_namespace != entry["namespace"]:
                continue
            if command in provided:
                replacements.setdefault(shim, []).append(
                    Candidate(
                        namespace=entry["namespace"],
                        name=entry["name"],
                        version=entry["version"],
                        language=manifest.package.language,
                        script=provided[command],
                    )
                )
    return replacements


def install_replacement(candidate: Candidate, shim: str, lock: dict, home: Path) -> None:
    """Point the convenience shim ``shim`` at ``candidate`` and record the
    ownership, taking it away from any current owner.

    This is the re-point primitive shared with `scripticus use`. The
    candidate's fully-qualified shim is (re)written too, so re-pointing is
    self-healing for trees whose shims predate D38.
    """
    bin_dir = home / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    script = home / "pkgs" / candidate.namespace / candidate.name / candidate.version / candidate.script
    target = fq_shim(candidate.namespace, candidate.name, shim_command(shim))
    _write_shim(bin_dir, target, script, candidate.language)
    _write_delegating_shim(bin_dir, shim, target)

    entry = _find_entry(lock, candidate.namespace, candidate.name)
    for other in lock["packages"]:
        if other is not entry and shim in other.get("shims", []):
            other["shims"].remove(shim)
    if entry is not None and shim not in entry.get("shims", []):
        entry["shims"] = sorted([*entry.get("shims", []), shim])
    write_lockfile(home, lock)


def apply_uninstall(entry: dict, lock: dict, home: Path) -> None:
    """Remove the package's shims and files, then drop it from the lockfile."""
    bin_dir = home / "bin"
    for command in entry["commands"]:
        _shim_path(bin_dir, fq_shim(entry["namespace"], entry["name"], command)).unlink(
            missing_ok=True
        )
    for shim in entry.get("shims", []):
        _shim_path(bin_dir, shim).unlink(missing_ok=True)

    package_dir = home / "pkgs" / entry["namespace"] / entry["name"] / entry["version"]
    shutil.rmtree(package_dir, ignore_errors=True)
    for parent in (package_dir.parent, package_dir.parent.parent):
        if parent.is_dir() and not any(parent.iterdir()):
            parent.rmdir()

    lock["packages"].remove(entry)
    write_lockfile(home, lock)
