"""Re-point a convenience command shim (`scripticus use`).

The manual escape hatch for last-install-wins shim collisions (D11), and
the explicit form of the uninstall-time replacement picker (D28) — both go
through the same re-point primitive, ``install_replacement``. The shim
argument's dot count selects the tier (D38): ``clash`` is the bare shim,
``acme.clash`` the namespaced one (re-pointable only within its
namespace). The fully-qualified tier is never re-pointed — it always
means the one thing it names.
"""

from pathlib import Path

from scripticus_schema.manifest import ManifestError, commands_of, load_manifest
from scripticus.uninstall import Candidate, UninstallError, find_installed


class UseError(Exception):
    """A command shim could not be re-pointed."""


def prepare_use(
    spec: str, shim: str, lock: dict, home: Path
) -> tuple[Candidate, dict | None]:
    """Resolve ``spec`` and check that re-pointing ``shim`` at it is legal.

    Returns the candidate to re-point at and the shim's current owner
    entry (``None`` if the shim has no owner, e.g. after an uninstall
    that orphaned it).
    """
    segments = shim.split(".")
    if len(segments) > 2 or not all(segments):
        raise UseError(
            f"'{shim}' is not a re-pointable shim — fully-qualified"
            " namespace.package.command shims always mean the one thing"
            " they name (re-point the bare or namespace.command form)"
        )
    required_namespace = segments[0] if len(segments) == 2 else None
    command = segments[-1]

    try:
        entry = find_installed(spec, lock)
    except UninstallError as exc:
        raise UseError(str(exc)) from exc

    package_id = f"{entry['namespace']}/{entry['name']}"
    if required_namespace is not None and entry["namespace"] != required_namespace:
        raise UseError(
            f"'{shim}' can only point at a package in the"
            f" '{required_namespace}' namespace, not {package_id}"
        )

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

    owner = next((e for e in lock["packages"] if shim in e.get("shims", [])), None)
    candidate = Candidate(
        namespace=entry["namespace"],
        name=entry["name"],
        version=entry["version"],
        language=manifest.package.language,
        script=commands[command],
    )
    return candidate, owner
