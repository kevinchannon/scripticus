"""Remote search: discover packages across the configured remotes (D48).

Search is the read path's discovery half — served entirely by each remote's
index service (Gitea's generic registry has no usable programmatic listing,
so the index database is authoritative for discovery). ``GET /search`` takes
a name substring ``q`` plus optional ``platform``/``language`` filters and
returns the matching packages, each at its latest non-yanked version (D30).

Unlike ``install`` (which stops at the first remote hosting the root, D46),
``search`` queries *every* configured remote in priority order and merges the
hits, each labelled with the remote it came from — the point is to see what is
out there, not to pick one. ``--remote`` restricts the search to a single
remote. Discovery is best-effort: a remote that is unreachable or errors is
reported as a warning and the other remotes' results still show; only an
all-remotes failure (or no remotes at all) is a hard error. No token is sent —
``/search`` is an anonymous read.
"""

from collections.abc import Callable
from dataclasses import dataclass

import httpx

from scripticus.config import Remote, find_remote
from scripticus_schema.index_api import PackageSummary, SearchResults


class SearchError(Exception):
    """A search could not be carried out at all (no remotes, unknown forced
    remote, or every queried remote failed)."""


@dataclass
class Hit:
    """One search result, tagged with the remote that returned it."""

    remote: str
    package: PackageSummary


@dataclass
class SearchOutcome:
    hits: list[Hit]
    warnings: list[str]  # per-remote failures that didn't sink the whole search


def _client() -> httpx.Client:
    # Seam for tests: monkeypatched with an httpx.MockTransport-backed client.
    return httpx.Client(timeout=30.0)


def _detail(response: httpx.Response) -> str:
    try:
        return response.json().get("detail", response.text)
    except ValueError:
        return response.text


def _get(remote: Remote, path: str, params: dict[str, str], verb: str) -> list[PackageSummary]:
    """GET a read endpoint returning ``SearchResults`` on one remote. Raises
    SearchError on transport or non-200 failure — the caller decides whether
    that sinks the whole query or just drops this remote."""
    try:
        with _client() as client:
            response = client.get(remote.url.rstrip("/") + path, params=params)
    except httpx.HTTPError as exc:
        raise SearchError(f"cannot reach '{remote.name}' ({remote.url}): {exc}") from exc
    if response.status_code != 200:
        raise SearchError(
            f"{verb} on '{remote.name}' failed ({response.status_code}): {_detail(response)}"
        )
    return SearchResults.model_validate(response.json()).results


def _fan_out(
    remotes: list[Remote],
    forced: str | None,
    fetch: "Callable[[Remote], list[PackageSummary]]",
) -> SearchOutcome:
    """Run ``fetch`` against the configured remotes (or the forced one),
    tagging each result with its remote in priority order. A remote that fails
    is collected as a warning; only an all-remotes failure is a hard
    SearchError. The shared fan-out/resilience policy for search and list
    (D48/D50)."""
    if forced is not None:
        remote = find_remote(remotes, forced)
        if remote is None:
            known = ", ".join(r.name for r in remotes) or "none"
            raise SearchError(f"no remote named '{forced}' (remotes: {known})")
        targets = [remote]
    else:
        if not remotes:
            raise SearchError(
                "no remotes configured — run 'scripticus login <name> <url>' first"
            )
        targets = remotes

    hits: list[Hit] = []
    warnings: list[str] = []
    for remote in targets:
        try:
            results = fetch(remote)
        except SearchError as exc:
            warnings.append(str(exc))
            continue
        hits.extend(Hit(remote.name, package) for package in results)

    if warnings and len(warnings) == len(targets):
        raise SearchError("; ".join(warnings))
    return SearchOutcome(hits=hits, warnings=warnings)


def search_remotes(
    remotes: list[Remote],
    forced: str | None,
    query: str,
    platform: str | None,
    language: str | None,
) -> SearchOutcome:
    """Content search (D48): match ``query`` against package name/description/
    command names across the remotes, merged in priority order."""
    params: dict[str, str] = {"q": query}
    if platform is not None:
        params["platform"] = platform
    if language is not None:
        params["language"] = language
    return _fan_out(remotes, forced, lambda remote: _get(remote, "/search", params, "search"))


def catalog_remotes(remotes: list[Remote], forced: str | None, glob: str | None) -> SearchOutcome:
    """Identity listing (D50): the packages whose ``namespace/name`` satisfies
    ``glob`` (server-side, via ``/packages``), merged across the remotes. The
    server owns the glob so its match agrees with the client's installed-side
    filtering."""
    params = {} if glob is None else {"glob": glob}
    return _fan_out(remotes, forced, lambda remote: _get(remote, "/packages", params, "listing"))
