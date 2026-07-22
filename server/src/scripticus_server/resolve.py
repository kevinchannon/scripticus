"""The read path's resolver: `POST /resolve` (D42/D43).

Given a root package, the client's platform, and the client's installed
closure (identities only — the server re-derives constraints from its own
index, D21/D33), produce the fully resolved closure (one version per
package) plus the aggregated tool requirements.

The solver works against an :class:`Index` abstraction, not the database
directly, so it is unit-tested with a fake index while the endpoint drives
the SQLAlchemy-backed :class:`DbIndex`. It is a backtracking search over
version choices: for each package it tries satisfying versions
highest-first (preferring an already-installed version), assigns, expands
that version's dependencies, and backtracks if a choice strands another
package. The graph is acyclic (D33), so the search terminates; trees are
shallow, so the worst case never bites. Installed packages enter two ways:
their own dependency constraints on closure packages are seeded as hard
constraints (a resolve never bumps a package in a way that breaks
something already installed), and an installed version is preferred when it
still satisfies (no needless churn).
"""

from collections import defaultdict
from collections.abc import Iterable
from typing import Protocol

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from scripticus_common.semver import semver_key
from scripticus_common.version_spec import VersionSpec, parse
from scripticus_schema.resolve_api import (
    InstalledPackage,
    ResolvedPackage,
    ResolvedTool,
    ResolveRequest,
    ResolveResult,
)
from scripticus_server import db
from scripticus_server.db import get_session

router = APIRouter()


class ResolutionError(Exception):
    """No consistent closure exists for the request."""


class Artifact(Protocol):
    content_hash: str
    download_pointer: str


class Index(Protocol):
    """The slice of the index the solver needs. Kept narrow so tests fake it."""

    def exists(self, package: str) -> bool: ...

    def candidates(self, package: str, platform: str) -> dict[str, "Artifact"]:
        """version -> artifact, for every non-yanked version of ``package``
        that has an artifact for ``platform``.
        """
        ...

    def dependencies(self, package: str, version: str) -> list[tuple[str, str]]:
        """(target, spec) for ``package@version``; ``[]`` if unknown (e.g. an
        installed package this index never published — D33 means it cannot
        constrain anything here anyway).
        """
        ...

    def tools(self, package: str, version: str) -> list[tuple[str, bool]]:
        """(tool name, required) for ``package@version``."""
        ...

    def commands(self, package: str, version: str) -> dict[str, str]:
        """command name -> script path for ``package@version`` (the index's
        publish-time projection, default entrypoint already applied).
        """
        ...


def _matches_all(specs: Iterable[VersionSpec], version: str) -> bool:
    return all(spec.matches(version) for spec in specs)


def _order_candidates(
    versions: Iterable[str], installed: str | None
) -> list[str]:
    """Highest semver first, but an installed version sorts ahead of its
    peers so it is tried (and thus preferred) before a bump.
    """
    return sorted(
        versions, key=lambda v: (v == installed, semver_key(v)), reverse=True
    )


def _solve(
    index: Index,
    platform: str,
    constraints: dict[str, tuple[str, ...]],
    assignment: dict[str, str],
    installed: dict[str, str],
    failure: dict,
) -> dict[str, str] | None:
    unassigned = sorted(p for p in constraints if p not in assignment)
    if not unassigned:
        return dict(assignment)

    package = unassigned[0]
    parsed = [parse(spec) for spec in constraints[package]]
    available = index.candidates(package, platform)
    if not available:
        failure["package"] = package
        failure["reason"] = f"has no artifact for platform '{platform}'"
        return None
    satisfying = [
        v for v in _order_candidates(available, installed.get(package))
        if _matches_all(parsed, v)
    ]
    if not satisfying:
        failure["package"] = package
        failure["reason"] = (
            f"no version satisfies {list(constraints[package])}"
            f" (available: {', '.join(sorted(available))})"
        )
        return None

    for version in satisfying:
        next_assignment = {**assignment, package: version}
        next_constraints = defaultdict(list, {k: list(v) for k, v in constraints.items()})
        for target, spec in index.dependencies(package, version):
            next_constraints[target].append(spec)
        frozen = {k: tuple(v) for k, v in next_constraints.items()}
        contradicted = _inconsistent_package(frozen, next_assignment)
        if contradicted is None:
            result = _solve(
                index, platform, frozen, next_assignment, installed, failure
            )
            if result is not None:
                return result
        else:
            # A newly added dependency contradicts an already-chosen version —
            # a genuine window conflict; record it in case the whole search
            # fails, so the error names the package and the clashing specs.
            failure["package"] = contradicted
            failure["reason"] = f"no version satisfies {list(frozen[contradicted])}"
    return None


def _inconsistent_package(
    constraints: dict[str, tuple[str, ...]], assignment: dict[str, str]
) -> str | None:
    """The first already-assigned package whose (possibly grown) constraints
    its chosen version no longer satisfies, or ``None`` if all still hold —
    prunes a branch whose new dependency contradicts an earlier choice.
    """
    for package, version in sorted(assignment.items()):
        if not _matches_all((parse(s) for s in constraints.get(package, ())), version):
            return package
    return None


def _topological(assignment: dict[str, str], index: Index) -> list[str]:
    """Packages ordered dependencies-before-dependents (post-order DFS; the
    graph is acyclic by D33).
    """
    deps_of = {
        package: [
            target
            for target, _ in index.dependencies(package, version)
            if target in assignment
        ]
        for package, version in assignment.items()
    }
    order: list[str] = []
    state: dict[str, str] = {}

    def visit(package: str) -> None:
        if state.get(package) == "done":
            return
        state[package] = "doing"
        for dependency in deps_of[package]:
            visit(dependency)
        state[package] = "done"
        order.append(package)

    for package in sorted(assignment):
        visit(package)
    return order


def resolve_closure(
    index: Index,
    root: str,
    spec: str,
    platform: str,
    installed: list[InstalledPackage],
) -> ResolveResult:
    installed_versions = {entry.package: entry.version for entry in installed}

    # Seed: installed packages' own constraints on any package are hard
    # constraints (don't break an installed dependent); the root carries the
    # requested spec.
    seed: dict[str, list[str]] = defaultdict(list)
    for package, version in installed_versions.items():
        for target, dep_spec in index.dependencies(package, version):
            seed[target].append(dep_spec)
    seed[root].append(spec or "*")

    failure: dict = {}
    assignment = _solve(
        index,
        platform,
        {k: tuple(v) for k, v in seed.items()},
        {},
        installed_versions,
        failure,
    )
    if assignment is None:
        if failure:
            raise ResolutionError(f"'{failure['package']}' {failure['reason']}")
        raise ResolutionError(f"could not resolve '{root}'")

    packages = []
    for package in _topological(assignment, index):
        version = assignment[package]
        artifact = index.candidates(package, platform)[version]
        namespace, _, name = package.partition("/")
        packages.append(
            ResolvedPackage(
                namespace=namespace,
                name=name,
                version=version,
                content_hash=artifact.content_hash,
                download_pointer=artifact.download_pointer,
                direct=(package == root),
                already_satisfied=installed_versions.get(package) == version,
                commands=index.commands(package, version),
            )
        )

    required: dict[str, bool] = {}
    for package, version in assignment.items():
        for name, is_required in index.tools(package, version):
            required[name] = required.get(name, False) or is_required
    tools = [
        ResolvedTool(name=name, required=required[name]) for name in sorted(required)
    ]

    return ResolveResult(packages=packages, tools=tools)


class _DbArtifact:
    def __init__(self, content_hash: str, download_pointer: str):
        self.content_hash = content_hash
        self.download_pointer = download_pointer


class DbIndex:
    """SQLAlchemy-backed :class:`Index` over the publish-time index tables."""

    def __init__(self, session: Session):
        self._session = session

    def _package(self, package: str) -> db.Package | None:
        namespace, _, name = package.partition("/")
        return self._session.scalar(
            select(db.Package)
            .join(db.Namespace)
            .where(db.Namespace.name == namespace, db.Package.name == name)
        )

    def _version(self, package: str, version: str) -> db.PackageVersion | None:
        found = self._package(package)
        if found is None:
            return None
        return next((pv for pv in found.versions if pv.version == version), None)

    def exists(self, package: str) -> bool:
        return self._package(package) is not None

    def candidates(self, package: str, platform: str) -> dict[str, _DbArtifact]:
        found = self._package(package)
        if found is None:
            return {}
        result: dict[str, _DbArtifact] = {}
        for pv in found.versions:
            if pv.yanked:
                continue
            artifact = next(
                (a for a in pv.artifacts if platform in a.platform_list()), None
            )
            if artifact is not None:
                result[pv.version] = _DbArtifact(
                    artifact.content_hash, artifact.gitea_pointer
                )
        return result

    def dependencies(self, package: str, version: str) -> list[tuple[str, str]]:
        pv = self._version(package, version)
        if pv is None:
            return []
        return [(dep.target, dep.spec) for dep in pv.dependencies]

    def tools(self, package: str, version: str) -> list[tuple[str, bool]]:
        pv = self._version(package, version)
        if pv is None:
            return []
        return [(dep.name, dep.required) for dep in pv.tool_deps]

    def commands(self, package: str, version: str) -> dict[str, str]:
        pv = self._version(package, version)
        if pv is None:
            return {}
        return {command.name: command.script_path for command in pv.commands}


@router.post("/resolve")
def resolve(
    request: ResolveRequest, session: Session = Depends(get_session)
) -> ResolveResult:
    index = DbIndex(session)
    if not index.exists(request.root):
        raise HTTPException(404, f"no package '{request.root}' in the index")
    try:
        return resolve_closure(
            index, request.root, request.spec, request.platform, request.installed
        )
    except ResolutionError as exc:
        raise HTTPException(422, str(exc)) from exc
