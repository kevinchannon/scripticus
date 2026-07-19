"""Re-point a command shim at a specific installed package (`scripticus use`).

The manual escape hatch for last-install-wins shim collisions (D11), and
the explicit form of the uninstall-time replacement picker (D28) — both go
through the same re-point primitive, ``install_replacement``.
"""

from pathlib import Path

from scripticus.manifest import ManifestError, commands_of, load_manifest
from scripticus.uninstall import Candidate, UninstallError, find_installed


class UseError(Exception):
    """A command shim could not be re-pointed."""


def prepare_use(
    spec: str, command: str, lock: dict, home: Path
) -> tuple[Candidate, dict | None]:
    """Resolve ``spec`` and check that it provides ``command``.

    Returns the candidate to re-point at and the command's current owner
    entry (``None`` if the command has no shim, e.g. after an uninstall
    that orphaned it).
    """
    try:
        entry = find_installed(spec, lock)
    except UninstallError as exc:
        raise UseError(str(exc)) from exc

    package_id = f"{entry['namespace']}/{entry['name']}"
    package_dir = home / "pkgs" / entry["namespace"] / entry["name"] / entry["version"]
    try:
        manifest = load_manifest(package_dir)
    except ManifestError as exc:
        raise UseError(f"installed tree of {package_id} is damaged: {exc}") from exc

    commands = commands_of(manifest)
    if command not in commands:
        provided = ", ".join(sorted(commands))
        raise UseError(
            f"{package_id} does not provide a command '{command}'"
            f" (it provides: {provided})"
        )

    owner = next((e for e in lock["packages"] if command in e["commands"]), None)
    candidate = Candidate(
        namespace=entry["namespace"],
        name=entry["name"],
        version=entry["version"],
        language=manifest["package"]["language"],
        script=commands[command],
    )
    return candidate, owner
