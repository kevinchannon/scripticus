"""Wire models for the resolution API (D42/D43).

`POST /resolve` takes a root package, the client's platform, and the
client's installed closure as **identities only** (name@version, no
constraints — the server re-derives them from its index, D21/D33), and
returns the fully resolved closure plus the aggregated tool requirements.
Only these shapes are contract; the solver's internals are not.
"""

from pydantic import BaseModel, Field


class InstalledPackage(BaseModel):
    """One entry of the client's installed closure: an identity only."""

    package: str  # "namespace/name"
    version: str


class ResolveRequest(BaseModel):
    root: str  # "namespace/name"
    spec: str = ""  # version spec (grammar in ARCHITECTURE.md); "" is latest
    platform: str  # the client's OS: the artifact variant is chosen for it
    installed: list[InstalledPackage] = Field(default_factory=list)


class ResolvedPackage(BaseModel):
    """One package in the resolved closure, at exactly one version (D42)."""

    namespace: str
    name: str
    version: str
    content_hash: str
    download_pointer: str  # Gitea path; the client fetches it directly (D9)
    direct: bool  # the root the user asked for (vs pulled in transitively)
    already_satisfied: bool  # the client already has this exact version


class ResolvedTool(BaseModel):
    """An aggregated system-tool requirement across the closure (D43). Name-
    only in v1; a version window is a fast-follow.
    """

    name: str
    required: bool  # required by at least one package (vs everywhere optional)


class ResolveResult(BaseModel):
    """The resolved closure (dependencies before dependents) and the tools it
    needs. ``packages`` is empty only if the root itself is already satisfied.
    """

    packages: list[ResolvedPackage] = Field(default_factory=list)
    tools: list[ResolvedTool] = Field(default_factory=list)
