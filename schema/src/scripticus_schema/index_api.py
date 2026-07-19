"""Wire models for the index service's read API (D30).

The first API schemas to be pinned down (D29 anticipated them): responses
for package version listing and search. The server builds them from the
index database; the client consumes them for `search` and version listing.

Contract notes encoded here rather than left to the server's discretion:
version listings are ordered newest-first by semver precedence (D16),
include yanked versions marked as such, and search results never include
yanked versions (npm-style yank).
"""

from pydantic import BaseModel, Field


class VersionSummary(BaseModel):
    version: str
    yanked: bool = False


class PackageVersions(BaseModel):
    """Response for a package's version listing.

    ``versions`` is ordered newest-first by semver precedence and includes
    yanked versions (marked), so a pinned/lockfile lookup can still see
    them; ``description`` comes from the latest non-yanked version.
    """

    namespace: str
    name: str
    description: str = ""
    versions: list[VersionSummary]


class PackageSummary(BaseModel):
    namespace: str
    name: str
    description: str = ""
    latest_version: str


class SearchResults(BaseModel):
    """Response for a search query. Yanked versions are invisible here: a
    package's ``latest_version`` is its latest non-yanked version, and a
    package with every version yanked does not appear at all.
    """

    results: list[PackageSummary] = Field(default_factory=list)
