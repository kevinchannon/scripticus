"""Updating installed remote packages (`scripticus update`).

`update [<pkg>...]` re-resolves installed remote-provenance packages against
their remotes, reusing install's plan/stage/apply back half (D42/D46); it
differs only in the resolve request — its targets are sent as roots with
``upgrade=True`` so they float to the newest satisfying version while every
other installed package stays put (D52). Bare `update` targets every direct
remote package; a named target must be an installed remote package.

Two things are update-specific and live here (D53):

- **Skipping locals.** A local (`-f`) install is in no index, so it cannot be
  updated; it is skipped with a warning (D20).
- **Grouping by remote.** A closure never spans remotes (D33), so targets are
  grouped by the remote they were installed from and each group is resolved
  independently against that remote.

Cleanup after a version whose command set shrank (dropped shims reconciled
through the uninstall picker) and the never-remove-tools advisory are also
computed here; the CLI drives the interactive parts.
"""

from dataclasses import dataclass
from pathlib import Path

from scripticus.install import shim_command
from scripticus_schema.manifest import ManifestError, load_manifest


class UpdateError(Exception):
    """An update could not be planned."""


@dataclass
class Targets:
    """The outcome of resolving the requested target names against the lock."""

    entries: list[dict]  # installed remote-provenance entries to update
    skipped_local: list[str]  # package ids skipped because they are local (D20)


def select_targets(lock: dict, names: list[str]) -> Targets:
    """Pick the lockfile entries to update.

    No names → every *direct* remote-provenance package (transitive deps move
    only as their dependents' constraints require them to). Named targets are
    matched by ``namespace/name`` or bare ``name`` (unambiguously, D5); a
    local-provenance match is skipped with a warning, an unknown one is an
    error.
    """
    remote_entries = [
        e for e in lock["packages"] if e.get("provenance", {}).get("type") == "remote"
    ]

    if not names:
        return Targets(entries=[e for e in remote_entries if e.get("direct")], skipped_local=[])

    entries: list[dict] = []
    skipped_local: list[str] = []
    for name in names:
        namespace, _, bare = name.rpartition("/")
        if namespace:
            matches = [
                e for e in lock["packages"]
                if e["namespace"] == namespace and e["name"] == bare
            ]
        else:
            matches = [e for e in lock["packages"] if e["name"] == bare]

        if not matches:
            raise UpdateError(f"'{name}' is not installed")
        if len(matches) > 1:
            ids = ", ".join(sorted(f"{e['namespace']}/{e['name']}" for e in matches))
            raise UpdateError(
                f"'{name}' matches more than one installed package ({ids})"
                " — use the namespace/name form"
            )
        entry = matches[0]
        if entry.get("provenance", {}).get("type") != "remote":
            skipped_local.append(f"{entry['namespace']}/{entry['name']}")
        else:
            entries.append(entry)
    return Targets(entries=entries, skipped_local=skipped_local)


def group_by_remote(entries: list[dict]) -> dict[str, list[str]]:
    """``remote name -> [package id]`` for the targets, so each remote's group
    is resolved independently (a closure is single-remote, D33)."""
    groups: dict[str, list[str]] = {}
    for entry in entries:
        remote = entry["provenance"]["remote"]
        groups.setdefault(remote, []).append(f"{entry['namespace']}/{entry['name']}")
    return groups


def dropped_convenience_shims(old_entry: dict, new_commands: dict[str, str]) -> list[str]:
    """The convenience shims ``old_entry`` owns for commands the new version no
    longer provides — the ones an update orphans and must reconcile (D53).

    The fully-qualified tier is uniquely owned and is simply removed by
    ``install_into_lock``; only these convenience shims need a replacement
    picker.
    """
    return sorted(
        shim
        for shim in old_entry.get("shims", [])
        if shim_command(shim) not in new_commands
    )


def required_tool_names(lock: dict, home: Path) -> set[str]:
    """Every system tool referenced (required or optional) by any installed
    package, re-derived from the installed manifests — the authoritative source
    (D21). Used to diff the tool needs across an update for the advisory (D53).
    """
    names: set[str] = set()
    for entry in lock["packages"]:
        package_dir = home / "pkgs" / entry["namespace"] / entry["name"] / entry["version"]
        try:
            manifest = load_manifest(package_dir)
        except ManifestError:
            continue
        tools = manifest.dependencies.tools
        names.update(tools.requires)
        names.update(tools.optional)
    return names
