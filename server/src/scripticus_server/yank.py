"""The write path for yank: flip a version's whole-version yanked flag (D54).

Unlike publish (D32/D37), yank is not an atomic batch, touches no Gitea blob,
and stages nothing: it is a single index-state mutation. The order is trivial —
authenticate against Gitea (D24), confirm the caller owns the namespace (the
same live ACL publish uses), locate the exact version, set the flag, commit.
Auth is checked before any lookup so a non-owner learns nothing about what the
index holds. There is no hard delete (D16): the artifacts and the version row
always remain; only visibility to search and `latest`/range resolution changes.
The same endpoint un-yanks (`yanked=false`, the client's `--undo`), because yank
is a reversible flag, not a one-way door — and there is no time window on it.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from scripticus_schema.yank_api import YankRequest, YankResult
from scripticus_server import db
from scripticus_server.db import get_session
from scripticus_server.gitea import (
    GiteaAuthError,
    GiteaClient,
    GiteaError,
    get_gitea_client,
)

router = APIRouter()


@router.patch("/packages/{namespace}/{name}/{version}")
def yank(
    namespace: str,
    name: str,
    version: str,
    request: YankRequest,
    session: Session = Depends(get_session),
    gitea: GiteaClient = Depends(get_gitea_client),
) -> YankResult:
    # Owner-authed like publish (D24/D32): only someone who could publish to
    # the namespace may change a version's visibility. Auth precedes the lookup
    # so a non-owner cannot probe which versions the index holds.
    try:
        user = gitea.authenticated_user()
        allowed = gitea.can_publish(namespace, user)
    except GiteaAuthError as exc:
        raise HTTPException(401, str(exc)) from exc
    except GiteaError as exc:
        raise HTTPException(502, str(exc)) from exc
    if not allowed:
        raise HTTPException(403, f"'{user}' cannot yank in namespace '{namespace}'")

    package_version = session.scalar(
        select(db.PackageVersion)
        .join(db.Package, db.PackageVersion.package_id == db.Package.id)
        .join(db.Namespace, db.Package.namespace_id == db.Namespace.id)
        .where(
            db.Namespace.name == namespace,
            db.Package.name == name,
            db.PackageVersion.version == version,
        )
    )
    if package_version is None:
        raise HTTPException(
            404, f"no version {version} of '{namespace}/{name}' in the index"
        )

    package_version.yanked = request.yanked
    session.commit()
    return YankResult(
        namespace=namespace, name=name, version=version, yanked=request.yanked
    )
