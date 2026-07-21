from typing import Literal

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from scripticus_schema.index_api import (
    PackageSummary,
    PackageVersions,
    SearchResults,
    VersionSummary,
)
from scripticus_schema.semver import semver_key
from scripticus_schema.whoami_api import WhoAmI
from scripticus_server import __version__, db
from scripticus_server.db import get_session
from scripticus_server.gitea import (
    GiteaAuthError,
    GiteaClient,
    GiteaError,
    get_gitea_client,
)
from scripticus_server.publish import router as publish_router

app = FastAPI(
    title="Scripticus index service",
    description=(
        "Manifest-aware search, version and dependency resolution, and the "
        "publish path for a Scripticus registry."
    ),
    version=__version__,
)
app.include_router(publish_router)


# Local to the server on purpose: a liveness shape is not part of the
# package contract, so it doesn't meet the schema/ admission rule (D29).
class HealthStatus(BaseModel):
    status: Literal["ok"] = "ok"


# Unauthenticated by design: a liveness probe carries nothing worth gating,
# and the index service stays out of the ACL business anyway (D24). Leave it
# open even once other endpoints grow auth.
@app.get("/health")
def health() -> HealthStatus:
    return HealthStatus()


class VersionInfo(BaseModel):
    version: str


@app.get("/version")
def version() -> VersionInfo:
    return VersionInfo(version=__version__)


# Pass-through token verification (D40): the caller's own Gitea token,
# checked live against Gitea (D24) exactly as publish does (D32). The
# service holds no credentials and stores nothing — the client uses this
# to verify a token at `login` time rather than at first publish (D34).
@app.get("/whoami")
def whoami(gitea: GiteaClient = Depends(get_gitea_client)) -> WhoAmI:
    try:
        return WhoAmI(username=gitea.authenticated_user())
    except GiteaAuthError as exc:
        raise HTTPException(401, str(exc)) from exc
    except GiteaError as exc:
        raise HTTPException(502, str(exc)) from exc


@app.get("/packages/{namespace}/{name}")
def package_versions(
    namespace: str, name: str, session: Session = Depends(get_session)
) -> PackageVersions:
    package = session.scalar(
        select(db.Package)
        .join(db.Namespace)
        .where(db.Namespace.name == namespace, db.Package.name == name)
    )
    if package is None:
        raise HTTPException(404, f"no package '{namespace}/{name}' in the index")
    ordered = sorted(
        package.versions, key=lambda pv: semver_key(pv.version), reverse=True
    )
    return PackageVersions(
        namespace=namespace,
        name=name,
        description=next((pv.description for pv in ordered if not pv.yanked), ""),
        versions=[
            VersionSummary(version=pv.version, yanked=pv.yanked) for pv in ordered
        ],
    )


def _has_matching_artifact(
    package_version: db.PackageVersion, platform: str | None, language: str | None
) -> bool:
    if platform is None and language is None:
        return True
    for artifact in package_version.artifacts:
        if platform is not None and platform not in artifact.platform_list():
            continue
        if language is not None and artifact.language != language:
            continue
        return True
    return False


@app.get("/search")
def search(
    q: str = "",
    platform: str | None = None,
    language: str | None = None,
    session: Session = Depends(get_session),
) -> SearchResults:
    packages = session.scalars(
        select(db.Package)
        .join(db.Namespace)
        .where(db.Package.name.contains(q))
        .order_by(db.Namespace.name, db.Package.name)
    ).all()
    results = []
    for package in packages:
        candidates = [
            pv
            for pv in package.versions
            if not pv.yanked and _has_matching_artifact(pv, platform, language)
        ]
        if not candidates:
            continue
        latest = max(candidates, key=lambda pv: semver_key(pv.version))
        results.append(
            PackageSummary(
                namespace=package.namespace.name,
                name=package.name,
                description=latest.description,
                latest_version=latest.version,
            )
        )
    return SearchResults(results=results)
