"""Registry + installed listing: ``list [glob]`` (D49).

dnf-style. ``list`` shows two sections — **Installed** (from the local
lockfile) and **Available** (the configured remotes' catalog, minus what's
already installed) — each filtered by an optional shell glob over the
package's ``namespace/name`` identity. ``--installed`` restricts to the
installed section (and touches no network); ``--available`` to the registry
section. Where ``search`` matches package *content* (D48), ``list``
enumerates *identity* deterministically.

The available half reuses ``search_remotes`` with an empty query to pull each
remote's whole catalog, then globs it client-side — fine at v1 registry scale
(D49). Unlike ``search``, ``list`` deduplicates an identity across remotes
(highest-priority remote wins), since enumerating the same package twice is
noise. A missing or unreachable registry degrades the ``list`` (both-section)
form to a warning rather than blanking the installed section it could still
show; ``--available`` on its own still errors, as there is nothing else to
show.
"""

import fnmatch
from dataclasses import dataclass, field
from pathlib import Path

from scripticus.config import Remote, find_remote
from scripticus.install import read_lockfile
from scripticus.search import SearchError, search_remotes


@dataclass
class Entry:
    namespace: str
    name: str
    version: str
    remote: str | None = None  # the source remote for an available row; None if installed

    @property
    def identity(self) -> str:
        return f"{self.namespace}/{self.name}"


@dataclass
class Listing:
    installed: list[Entry]
    available: list[Entry]
    warnings: list[str] = field(default_factory=list)


def _matches(pattern: str | None, namespace: str, name: str) -> bool:
    """Shell-glob a package's identity. A pattern containing ``/`` matches the
    full ``namespace/name`` (so ``acme/*`` scopes by namespace); a bare pattern
    matches the name alone (so ``db-*`` finds it in any namespace). No pattern
    matches everything."""
    if not pattern:
        return True
    target = f"{namespace}/{name}" if "/" in pattern else name
    return fnmatch.fnmatch(target, pattern)


def build_listing(
    home: Path,
    remotes: list[Remote],
    forced: str | None,
    glob: str | None,
    scope: str,  # "all" | "installed" | "available"
) -> Listing:
    """Assemble the installed and/or available sections for ``scope``, glob-
    filtered. Raises SearchError on an unknown forced remote always, and on a
    missing/failed registry only when the registry is all that was asked for
    (``--available``); the both-section form degrades those to warnings."""
    if forced is not None and scope in ("all", "available") and find_remote(remotes, forced) is None:
        known = ", ".join(r.name for r in remotes) or "none"
        raise SearchError(f"no remote named '{forced}' (remotes: {known})")

    lock = read_lockfile(home)
    installed_ids = {(entry["namespace"], entry["name"]) for entry in lock["packages"]}

    installed: list[Entry] = []
    if scope in ("all", "installed"):
        installed = [
            Entry(entry["namespace"], entry["name"], entry["version"])
            for entry in lock["packages"]
            if _matches(glob, entry["namespace"], entry["name"])
        ]
        installed.sort(key=lambda e: (e.namespace, e.name))

    available: list[Entry] = []
    warnings: list[str] = []
    if scope in ("all", "available"):
        try:
            outcome = search_remotes(remotes, forced, "", None, None)
        except SearchError as exc:
            if scope == "available":
                raise
            warnings = [str(exc)]
        else:
            warnings = outcome.warnings
            seen: set[tuple[str, str]] = set()
            for hit in outcome.hits:
                key = (hit.package.namespace, hit.package.name)
                if key in installed_ids or key in seen or not _matches(glob, *key):
                    continue
                seen.add(key)
                available.append(
                    Entry(hit.package.namespace, hit.package.name, hit.package.latest_version, hit.remote)
                )
            available.sort(key=lambda e: (e.namespace, e.name))

    return Listing(installed=installed, available=available, warnings=warnings)
