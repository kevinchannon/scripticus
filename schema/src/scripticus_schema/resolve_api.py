"""Wire models for the resolution API (D42/D43/D52).

`POST /resolve` takes one or more roots, the client's platform, and the
client's installed closure as **identities only** (name@version, no
constraints — the server re-derives them from its index, D21/D33), and
returns the fully resolved closure plus the aggregated tool requirements.
Only these shapes are contract; the solver's internals are not.

`install` sends a single root with ``upgrade=False``; `update` sends its
targets as roots with ``upgrade=True`` (D52). With ``upgrade`` set, a root
that is also in the installed closure is dropped from the solver's
installed-version preference set, so it floats to the newest satisfying
version; without it an installed root stays at its version when it still
satisfies (pip's "already satisfied", no silent upgrade). Non-root installed
packages stay preferred either way.
"""

from pydantic import BaseModel, Field


class InstalledPackage(BaseModel):
    """One entry of the client's installed closure: an identity only."""

    package: str  # "namespace/name"
    version: str


class Root(BaseModel):
    """A package the caller is asking to (re)resolve, with an optional spec."""

    package: str  # "namespace/name"
    spec: str = ""  # version spec (grammar in ARCHITECTURE.md); "" is latest


class ResolveRequest(BaseModel):
    roots: list[Root]  # install sends one; update sends its targets (D52)
    platform: str  # the client's OS: the artifact variant is chosen for it
    installed: list[InstalledPackage] = Field(default_factory=list)
    upgrade: bool = False  # float installed roots to newest (update, D52)


class HeldBack(BaseModel):
    """Why a root did not reach the newest version that exists (D52).

    Populated by the resolver's post-solve diagnostic when a root's chosen
    version is below the highest one its requested spec would allow, so the
    client can explain a held-back update ("foo held at 1.5 — 2.0 available,
    blocked by acme/lib: no version satisfies ['^2', '^1']") instead of
    silently reporting no update.
    """

    available: str  # the highest version the root's own spec allows but wasn't taken
    blocked_by: str  # "namespace/name" at the centre of the blocking conflict
    detail: str  # human-readable reason (the solver's failure reason)


class ResolvedPackage(BaseModel):
    """One package in the resolved closure, at exactly one version (D42)."""

    namespace: str
    name: str
    version: str
    content_hash: str
    download_pointer: str  # Gitea path; the client fetches it directly (D9)
    direct: bool  # the root the user asked for (vs pulled in transitively)
    already_satisfied: bool  # the client already has this exact version
    # The effective command -> script-path map (the index's projection of the
    # manifest, default-entrypoint rule applied at publish). Carried in the
    # response so the client can present the D17 transaction summary's shim
    # conflicts *before* prompting, without fetching the archive first — which
    # keeps D42's fetch-after-prompt boundary intact (D47).
    commands: dict[str, str] = Field(default_factory=dict)
    # Set only for a root the solver could not take to its newest version (D52).
    held_back: HeldBack | None = None


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
