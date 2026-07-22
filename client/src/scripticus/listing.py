"""Registry + installed listing: ``list [glob]`` (D49).

dnf-style. ``list`` shows two sections — **Installed** (from the local
lockfile) and **Available** (the configured remotes' catalog, minus what's
already installed) — each filtered by an optional shell glob over the
package's ``namespace/name`` identity. ``--installed`` restricts to the
installed section (and touches no network); ``--available`` to the registry
section. Where ``search`` matches package *content* (D48), ``list``
enumerates *identity* deterministically.

The available half asks each remote's ``GET /packages`` to apply the glob
server-side (D50), so the client no longer downloads the whole catalog. The
installed half is globbed on the client, against the lockfile — both sides use
the same ``scripticus_common.identity_glob`` primitive so their matches agree
exactly. Unlike ``search``, ``list`` deduplicates an identity across remotes
(highest-priority remote wins), since enumerating the same package twice is
noise. A missing or unreachable registry degrades the ``list`` (both-section)
form to a warning rather than blanking the installed section it could still
show; ``--available`` on its own still errors, as there is nothing else to
show.
"""

from dataclasses import dataclass, field
from pathlib import Path

from scripticus.config import Remote, find_remote
from scripticus.install import read_lockfile
from scripticus.search import SearchError, catalog_remotes
from scripticus_common.identity_glob import matches as identity_matches


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
            if identity_matches(glob, entry["namespace"], entry["name"])
        ]
        installed.sort(key=lambda e: (e.namespace, e.name))

    available: list[Entry] = []
    warnings: list[str] = []
    if scope in ("all", "available"):
        try:
            # The remote applies the glob (D50); the client only excludes what's
            # installed and dedupes an identity across remotes.
            outcome = catalog_remotes(remotes, forced, glob)
        except SearchError as exc:
            if scope == "available":
                raise
            warnings = [str(exc)]
        else:
            warnings = outcome.warnings
            seen: set[tuple[str, str]] = set()
            for hit in outcome.hits:
                key = (hit.package.namespace, hit.package.name)
                if key in installed_ids or key in seen:
                    continue
                seen.add(key)
                available.append(
                    Entry(hit.package.namespace, hit.package.name, hit.package.latest_version, hit.remote)
                )
            available.sort(key=lambda e: (e.namespace, e.name))

    return Listing(installed=installed, available=available, warnings=warnings)
