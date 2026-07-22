"""The write path: atomic server-mediated batch publish (D8, D32, D37).

`POST /packages` accepts one or more archives — a version's whole
format-group set — in a single request. Order of operations is the
atomicity guarantee: every archive is staged and validated, and the
batch-wide cohesion and D33 dependency checks pass, before any Gitea
write; only once every archive has validated does the server start
uploading blobs; only once every upload is confirmed does it commit the
index record(s). Failure anywhere rejects the whole batch: no blob is
uploaded, nothing is committed. A failure after some blobs have already
been uploaded (a later upload fails, or the index commit fails) triggers
a best-effort delete of every blob this batch uploaded, so nothing
dangles.

Nothing the client claims is trusted (D8): identity, variant tags,
dependencies, and the content hash are all derived from the uploaded
archives server-side.
"""

import re
import tarfile
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from scripticus_common.treehash import tree_hash
from scripticus_schema.manifest import (
    FORMAT_GROUPS,
    NAMESPACE_RE,
    PACKAGE_NAME_RE,
    Manifest,
    ManifestError,
    PackageMeta,
    commands_of,
    load_manifest,
)
from scripticus_schema.publish_api import PublishedArtifact, PublishResult
from scripticus_server import db
from scripticus_server.db import get_session
from scripticus_server.gitea import (
    GiteaAuthError,
    GiteaClient,
    GiteaError,
    get_gitea_client,
)

router = APIRouter()

RESERVED_NAMESPACES = ("library",)

_SAFE_FILENAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _archive_format(filename: str) -> str:
    if filename.endswith((".tar.gz", ".tgz")):
        return "tar.gz"
    if filename.endswith(".zip"):
        return "zip"
    raise HTTPException(400, f"'{filename}' is not a supported archive (.tar.gz or .zip)")


def _extract_archive(archive: Path, destination: Path, label: str) -> Path:
    """Extract and return the single root directory of the package tree.

    The tar "data" filter (PEP 706) rejects path traversal, links pointing
    outside the tree, and device files; zipfile's extract sanitises
    absolute paths and parent references itself. ``label`` (the archive's
    upload filename) is included in any error so a batch failure can be
    attributed to the right archive.
    """
    try:
        if archive.name.endswith(".zip"):
            with zipfile.ZipFile(archive) as zf:
                zf.extractall(destination)
        else:
            with tarfile.open(archive) as tar:
                tar.extractall(destination, filter="data")
    except (tarfile.TarError, zipfile.BadZipFile, OSError) as exc:
        raise HTTPException(400, f"'{label}': cannot extract archive: {exc}") from exc

    roots = list(destination.iterdir())
    if len(roots) != 1 or not roots[0].is_dir():
        raise HTTPException(
            400, f"'{label}': archive does not contain a single package directory"
        )
    return roots[0]


def _artifact_platforms(manifest: Manifest, archive_format: str, label: str) -> list[str]:
    group = dict(FORMAT_GROUPS)[archive_format]
    platforms = [os_name for os_name in group if os_name in manifest.platforms.os]
    if not platforms:
        raise HTTPException(
            422,
            f"'{label}': a .{archive_format} archive carries {'/'.join(group)} targets,"
            f" but the manifest declares platforms {manifest.platforms.os}",
        )
    return platforms


def _check_dependency_targets(session: Session, manifest: Manifest) -> None:
    """Publish-time dependency rules (D33): targets must be fully namespaced
    and already present in the index.
    """
    missing = []
    for target in sorted(manifest.dependencies.packages):
        namespace, _, name = target.partition("/")
        if not (NAMESPACE_RE.match(namespace) and name and PACKAGE_NAME_RE.match(name)):
            raise HTTPException(
                422,
                f"dependency '{target}' is not a fully namespaced package"
                " reference (namespace/name)",
            )
        known = session.scalar(
            select(db.Package)
            .join(db.Namespace)
            .where(db.Namespace.name == namespace, db.Package.name == name)
        )
        if known is None or not known.versions:
            missing.append(target)
    if missing:
        raise HTTPException(
            422,
            "dependencies not present in the index: " + ", ".join(missing),
        )


def _check_no_cycle(session: Session, manifest: Manifest) -> None:
    """Reject a publish that would make the publishing package reachable from
    its own dependencies (D33). Edges are package-level: the union of every
    version's declared dependencies.
    """
    rows = session.execute(
        select(db.Namespace.name, db.Package.name, db.Dependency.target)
        .join(db.Package, db.Package.namespace_id == db.Namespace.id)
        .join(db.PackageVersion, db.PackageVersion.package_id == db.Package.id)
        .join(db.Dependency, db.Dependency.package_version_id == db.PackageVersion.id)
    ).all()
    edges: dict[str, set[str]] = {}
    for namespace, name, target in rows:
        edges.setdefault(f"{namespace}/{name}", set()).add(target)

    publishing = f"{manifest.package.namespace}/{manifest.package.name}"
    edges.setdefault(publishing, set()).update(manifest.dependencies.packages)

    seen = set()
    stack = list(edges[publishing])
    while stack:
        node = stack.pop()
        if node == publishing:
            raise HTTPException(
                422,
                f"publishing would create a dependency cycle back to '{publishing}'",
            )
        if node in seen:
            continue
        seen.add(node)
        stack.extend(edges.get(node, ()))


def _storage_filename(upload_name: str, manifest: Manifest, archive_format: str) -> str:
    """The blob's filename in Gitea. The client's wheel-style name is kept
    when sane (filenames are human-legible redundancy only — the manifest is
    the source of truth), otherwise a minimal name is generated.
    """
    candidate = Path(upload_name).name
    if _SAFE_FILENAME_RE.match(candidate) and candidate.endswith(f".{archive_format}"):
        return candidate
    package = manifest.package
    return (
        f"{package.name.replace('-', '_')}-{package.version.replace('-', '_')}"
        f".{archive_format}"
    )


@dataclass
class _StagedArchive:
    """One archive of a batch, fully validated and read into memory — safe
    to use after the staging temp directory it was extracted into is gone.
    """

    upload_name: str
    data: bytes
    archive_format: str
    manifest: Manifest
    content_hash: str
    manifest_text: str
    platforms: list[str]
    filename: str


def _stage_and_validate(archive: UploadFile, work_dir: Path) -> _StagedArchive:
    upload_name = Path(archive.filename or "").name
    data = archive.file.read()
    archive_format = _archive_format(upload_name)

    work_dir.mkdir(parents=True)
    upload_path = work_dir / f"upload.{archive_format}"
    upload_path.write_bytes(data)
    package_root = _extract_archive(upload_path, work_dir / "tree", upload_name)

    try:
        manifest = load_manifest(package_root)
    except ManifestError as exc:
        raise HTTPException(422, f"'{upload_name}': {exc}") from exc

    platforms = _artifact_platforms(manifest, archive_format, upload_name)
    content_hash = tree_hash(package_root)
    manifest_text = (package_root / "meta.toml").read_text()
    filename = _storage_filename(upload_name, manifest, archive_format)

    return _StagedArchive(
        upload_name=upload_name,
        data=data,
        archive_format=archive_format,
        manifest=manifest,
        content_hash=content_hash,
        manifest_text=manifest_text,
        platforms=platforms,
        filename=filename,
    )


def _check_batch_cohesion(staged: list[_StagedArchive]) -> None:
    """D37: every archive in a batch is one tree in different containers —
    the format-variant rule's shared-content-hash invariant, enforced here
    rather than merely assumed, plus no two archives may claim the same
    format.
    """
    hashes = {s.content_hash for s in staged}
    if len(hashes) > 1:
        detail = ", ".join(f"'{s.upload_name}' ({s.content_hash})" for s in staged)
        raise HTTPException(
            422,
            "archives in one batch must be the same content in different"
            f" containers (D26 format variants): {detail}",
        )

    format_owner: dict[str, str] = {}
    for s in staged:
        prior = format_owner.get(s.archive_format)
        if prior is not None:
            raise HTTPException(
                422,
                f"batch contains two .{s.archive_format} archives:"
                f" '{prior}' and '{s.upload_name}'",
            )
        format_owner[s.archive_format] = s.upload_name


def _rollback_uploads(
    gitea: GiteaClient, meta: PackageMeta, uploaded: list[tuple[_StagedArchive, str]]
) -> None:
    """Best-effort cleanup of every blob this batch already put in Gitea."""
    for s, _pointer in uploaded:
        gitea.delete_blob(meta.namespace, meta.name, meta.version, s.filename)


@router.post("/packages", status_code=201)
def publish(
    archives: list[UploadFile],
    session: Session = Depends(get_session),
    gitea: GiteaClient = Depends(get_gitea_client),
) -> PublishResult:
    if not archives:
        raise HTTPException(400, "a publish request must contain at least one archive")

    with tempfile.TemporaryDirectory(prefix="scripticus-publish-") as staging_dir:
        staging = Path(staging_dir)
        staged = [
            _stage_and_validate(archive, staging / str(i))
            for i, archive in enumerate(archives)
        ]
        _check_batch_cohesion(staged)

        manifest = staged[0].manifest
        content_hash = staged[0].content_hash
        meta = manifest.package

        if meta.namespace in RESERVED_NAMESPACES:
            raise HTTPException(403, f"the '{meta.namespace}' namespace is reserved")

        try:
            user = gitea.authenticated_user()
            allowed = gitea.can_publish(meta.namespace, user)
        except GiteaAuthError as exc:
            raise HTTPException(401, str(exc)) from exc
        except GiteaError as exc:
            raise HTTPException(502, str(exc)) from exc
        if not allowed:
            raise HTTPException(
                403, f"'{user}' cannot publish to namespace '{meta.namespace}'"
            )

    namespace = session.scalar(
        select(db.Namespace).where(db.Namespace.name == meta.namespace)
    )
    package = session.scalar(
        select(db.Package)
        .join(db.Namespace)
        .where(db.Namespace.name == meta.namespace, db.Package.name == meta.name)
    )
    existing = None
    if package is not None:
        existing = next(
            (pv for pv in package.versions if pv.version == meta.version), None
        )
    if existing is not None:
        # The variant rule (D32): more artifacts may join a version only if
        # they carry the identical tree, in a format not yet published.
        recorded_hashes = {a.content_hash for a in existing.artifacts}
        if recorded_hashes and content_hash not in recorded_hashes:
            raise HTTPException(
                409,
                f"{meta.namespace}/{meta.name} {meta.version} already exists"
                " with different content; versions are immutable",
            )
        existing_formats = {a.archive_format for a in existing.artifacts}
        duplicates = [s for s in staged if s.archive_format in existing_formats]
        if duplicates:
            detail = ", ".join(f"'{s.upload_name}' (.{s.archive_format})" for s in duplicates)
            raise HTTPException(
                409,
                f"{meta.namespace}/{meta.name} {meta.version} already has an"
                f" artifact in that format; duplicate versions are rejected: {detail}",
            )
    else:
        _check_dependency_targets(session, manifest)
        _check_no_cycle(session, manifest)

    # Every archive in the batch has validated: only now may Gitea writes
    # start. uploaded tracks what succeeded so a later failure can unwind it.
    uploaded: list[tuple[_StagedArchive, str]] = []
    try:
        for s in staged:
            pointer = gitea.upload_blob(
                meta.namespace, meta.name, meta.version, s.filename, s.data
            )
            uploaded.append((s, pointer))
    except GiteaAuthError as exc:
        _rollback_uploads(gitea, meta, uploaded)
        raise HTTPException(401, str(exc)) from exc
    except GiteaError as exc:
        _rollback_uploads(gitea, meta, uploaded)
        raise HTTPException(502, str(exc)) from exc

    # Every blob is in Gitea; only now may the index record go in.
    try:
        if existing is None:
            if namespace is None:
                namespace = db.Namespace(name=meta.namespace)
            if package is None:
                package = db.Package(namespace=namespace, name=meta.name)
            version = db.PackageVersion(
                package=package,
                version=meta.version,
                description=meta.description,
                published_at=datetime.now(timezone.utc).isoformat(),
                publisher=user,
            )
            for target, spec in sorted(manifest.dependencies.packages.items()):
                version.dependencies.append(db.Dependency(target=target, spec=spec))
            for tool in manifest.dependencies.tools.requires:
                version.tool_deps.append(db.ToolDep(name=tool, required=True))
            for tool in manifest.dependencies.tools.optional:
                version.tool_deps.append(db.ToolDep(name=tool, required=False))
            for command, script in sorted(commands_of(manifest).items()):
                version.commands.append(db.Command(name=command, script_path=script))
            db.ManifestBlob(package_version=version, toml=staged[0].manifest_text)
        else:
            version = existing
        for s, pointer in uploaded:
            db.Artifact(
                package_version=version,
                platforms=",".join(s.platforms),
                language=meta.language,
                archive_format=s.archive_format,
                content_hash=s.content_hash,
                size=len(s.data),
                gitea_pointer=pointer,
            )
        session.add(version)
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        _rollback_uploads(gitea, meta, uploaded)
        raise HTTPException(
            409,
            f"{meta.namespace}/{meta.name} {meta.version} was published"
            " concurrently; duplicate versions are rejected",
        ) from exc
    except Exception:
        session.rollback()
        _rollback_uploads(gitea, meta, uploaded)
        raise

    return PublishResult(
        namespace=meta.namespace,
        name=meta.name,
        version=meta.version,
        content_hash=content_hash,
        publisher=user,
        artifacts=[
            PublishedArtifact(
                filename=s.filename,
                archive_format=s.archive_format,
                platforms=s.platforms,
                language=meta.language,
                size=len(s.data),
            )
            for s in staged
        ],
    )
