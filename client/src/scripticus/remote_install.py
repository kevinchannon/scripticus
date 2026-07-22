"""Remote install: resolve a closure from a remote, then fetch and install it
(D42/D46).

Two phases, with the plan/confirm/prompt boundary between them (D42):

1. **Resolve.** ``install <namespace/name>[@spec]`` asks the configured
   remotes, in priority order, to resolve the root and its whole transitive
   closure — stopping at the first remote whose index has the root (``--remote``
   forces one). The request carries the client's platform and its installed
   closure as identities only (remote-provenance packages; the server
   re-derives their constraints, D42). A closure never spans remotes (D33/D46),
   so one ``/resolve`` call is complete. The response's per-package command map
   (D47) lets the plan show D17's shim conflicts without fetching anything.
2. **Fetch and install.** After the transaction is confirmed, every to-install
   blob is fetched straight from the remote's front URL (``{remote.url}{pointer}``,
   routed to Gitea by the proxy, D45) with the stored token, its tree hash
   verified against the resolved hash (D3), and the whole set staged before any
   of it is unpacked (stage-then-commit, so a mid-fetch failure never leaves a
   partial install, D17). System tools install first (D43/D44), before any
   package file or shim is written.
"""

import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from scripticus.config import Remote, find_remote
from scripticus.install import (
    Conflict,
    Tool,
    _extract_archive,
    _find_entry,
    convenience_shims,
    install_into_lock,
    read_lockfile,
    write_lockfile,
)
from scripticus_common.semver import semver_key
from scripticus_common.treehash import tree_hash
from scripticus_common.version_spec import VersionSpecError, parse as parse_spec
from scripticus_schema.manifest import Manifest, load_manifest
from scripticus_schema.resolve_api import (
    InstalledPackage,
    ResolvedPackage,
    ResolveRequest,
    ResolveResult,
)


class RemoteInstallError(Exception):
    """A remote install could not be planned or carried out."""


class ResolveError(RemoteInstallError):
    """A remote hosts the root but cannot satisfy the request (unsatisfiable
    window, single-version-per-closure conflict, missing platform variant)."""


class DownloadError(RemoteInstallError):
    """A resolved blob could not be fetched."""


class IntegrityError(RemoteInstallError):
    """A downloaded blob does not match its resolved content hash."""


# --- Parsing the target ----------------------------------------------------


def parse_target(target: str) -> tuple[str, str]:
    """Split ``namespace/name[@spec]`` into ``("namespace/name", spec)``.

    v1 requires the fully-namespaced form (D46); a bare name is rejected until
    the namespace search path is designed (D5). The spec, if present, is
    validated against the version-spec grammar.
    """
    root, _, spec = target.partition("@")
    if root.count("/") != 1 or not all(root.split("/")):
        raise RemoteInstallError(
            f"'{target}' is not a fully-namespaced package name"
            " — remote install needs 'namespace/name[@version]'"
            " (bare-name install is not supported yet)"
        )
    if spec:
        try:
            parse_spec(spec)
        except VersionSpecError as exc:
            raise RemoteInstallError(f"invalid version spec '@{spec}': {exc}") from exc
    return root, spec


def installed_closure(lock: dict) -> list[InstalledPackage]:
    """The installed packages to send to ``/resolve`` as identities (D42).

    Only remote-provenance packages participate: a local (`-f`) install is in
    no index, cannot declare package dependencies, and so constrains nothing.
    """
    return [
        InstalledPackage(package=f"{entry['namespace']}/{entry['name']}", version=entry["version"])
        for entry in lock["packages"]
        if entry.get("provenance", {}).get("type") == "remote"
    ]


# --- Resolving against the remotes -----------------------------------------


def _client() -> httpx.Client:
    # Seam for tests: monkeypatched with an httpx.MockTransport-backed client.
    return httpx.Client(timeout=30.0)


def _detail(response: httpx.Response) -> str:
    try:
        return response.json().get("detail", response.text)
    except ValueError:
        return response.text


def _resolve_on(remote: Remote, request: ResolveRequest) -> ResolveResult | None:
    """POST ``/resolve`` to one remote. ``None`` when the remote's index does
    not have the root (404, so the search moves on); raises ResolveError on an
    unsatisfiable closure (422); raises RemoteInstallError on transport or
    other failures.
    """
    try:
        with _client() as client:
            response = client.post(
                remote.url.rstrip("/") + "/resolve", json=request.model_dump()
            )
    except httpx.HTTPError as exc:
        raise RemoteInstallError(
            f"cannot reach '{remote.name}' ({remote.url}): {exc}"
        ) from exc

    if response.status_code == 404:
        return None
    if response.status_code == 422:
        raise ResolveError(f"'{remote.name}' cannot satisfy the request: {_detail(response)}")
    if response.status_code != 200:
        raise RemoteInstallError(
            f"resolve against '{remote.name}' failed ({response.status_code}): {_detail(response)}"
        )
    return ResolveResult.model_validate(response.json())


def resolve_root(
    remotes: list[Remote],
    forced: str | None,
    root: str,
    spec: str,
    platform: str,
    installed: list[InstalledPackage],
) -> tuple[Remote, ResolveResult]:
    """Resolve ``root`` against the remotes in priority order (or the forced
    one), returning the resolving remote and its closure. Stops at the first
    remote whose index has the root (D46).
    """
    request = ResolveRequest(root=root, spec=spec, platform=platform, installed=installed)

    if forced is not None:
        remote = find_remote(remotes, forced)
        if remote is None:
            known = ", ".join(r.name for r in remotes) or "none"
            raise RemoteInstallError(f"no remote named '{forced}' (remotes: {known})")
        result = _resolve_on(remote, request)
        if result is None:
            raise RemoteInstallError(f"remote '{remote.name}' has no package '{root}'")
        return remote, result

    if not remotes:
        raise RemoteInstallError(
            "no remotes configured — run 'scripticus login <name> <url>' first"
        )
    for remote in remotes:
        result = _resolve_on(remote, request)
        if result is not None:
            return remote, result
    tried = ", ".join(r.name for r in remotes)
    raise RemoteInstallError(f"no configured remote has package '{root}' (tried: {tried})")


# --- Planning --------------------------------------------------------------


@dataclass
class PackageAction:
    resolved: ResolvedPackage
    action: str  # install | upgrade | downgrade | reinstall
    installed_version: str | None


@dataclass
class RemotePlan:
    remote: Remote
    token: str
    result: ResolveResult
    actions: list[PackageAction]  # excludes already-satisfied packages (D17)
    conflicts: list[Conflict] = field(default_factory=list)
    required_tools: list[Tool] = field(default_factory=list)
    optional_tools: list[Tool] = field(default_factory=list)

    @property
    def missing_required(self) -> list[str]:
        return [tool.name for tool in self.required_tools if not tool.found]

    @property
    def root_version(self) -> str | None:
        return next((p.version for p in self.result.packages if p.direct), None)


def _classify(resolved: ResolvedPackage, lock: dict) -> PackageAction:
    entry = _find_entry(lock, resolved.namespace, resolved.name)
    if entry is None:
        action = "install"
    elif entry["version"] == resolved.version:
        action = "reinstall"
    elif semver_key(resolved.version) > semver_key(entry["version"]):
        action = "upgrade"
    else:
        action = "downgrade"
    return PackageAction(resolved, action, entry["version"] if entry else None)


def build_plan(
    remote: Remote, token: str, result: ResolveResult, lock: dict
) -> RemotePlan:
    """Turn a resolved closure into a transaction plan against the current
    lockfile: the actions (already-satisfied packages omitted, D17), the shim
    conflicts against packages outside the closure, and the tool presence
    check (D43)."""
    actions = [
        _classify(resolved, lock)
        for resolved in result.packages
        if not resolved.already_satisfied
    ]

    closure_ids = {(p.namespace, p.name) for p in result.packages}
    incoming: set[str] = set()
    for resolved in result.packages:
        if not resolved.already_satisfied:
            incoming |= set(convenience_shims(resolved.namespace, resolved.commands))
    conflicts = [
        Conflict(shim, f"{entry['namespace']}/{entry['name']} {entry['version']}")
        for entry in lock["packages"]
        if (entry["namespace"], entry["name"]) not in closure_ids
        for shim in entry.get("shims", [])
        if shim in incoming
    ]

    required = sorted({t.name for t in result.tools if t.required})
    optional = sorted({t.name for t in result.tools if not t.required})
    required_tools = [Tool(name, shutil.which(name) is not None) for name in required]
    optional_tools = [Tool(name, shutil.which(name) is not None) for name in optional]

    return RemotePlan(
        remote=remote,
        token=token,
        result=result,
        actions=actions,
        conflicts=conflicts,
        required_tools=required_tools,
        optional_tools=optional_tools,
    )


# --- Fetch, verify, stage --------------------------------------------------


@dataclass
class Staged:
    resolved: ResolvedPackage
    package_root: Path
    manifest: Manifest


def _stage_one(
    remote: Remote, token: str, resolved: ResolvedPackage, staging_root: Path
) -> Staged:
    url = remote.url.rstrip("/") + resolved.download_pointer
    package_id = f"{resolved.namespace}/{resolved.name}"
    try:
        with _client() as client:
            response = client.get(url, headers={"Authorization": f"token {token}"})
    except httpx.HTTPError as exc:
        raise DownloadError(f"cannot download {package_id}: {exc}") from exc
    if response.status_code == 401:
        raise DownloadError(
            f"'{remote.name}' rejected the token downloading {package_id}"
            f" — run 'scripticus login {remote.name}' with a fresh Gitea token"
        )
    if response.status_code != 200:
        raise DownloadError(
            f"downloading {package_id} failed ({response.status_code}): {_detail(response)}"
        )

    package_dir = Path(tempfile.mkdtemp(dir=staging_root))
    filename = resolved.download_pointer.rsplit("/", 1)[-1] or "archive"
    archive_path = package_dir / filename
    archive_path.write_bytes(response.content)
    tree_dest = package_dir / "tree"
    tree_dest.mkdir()
    package_root = _extract_archive(archive_path, tree_dest)

    got = tree_hash(package_root)
    if got != resolved.content_hash:
        raise IntegrityError(
            f"content hash mismatch for {package_id}@{resolved.version}:"
            f" resolved {resolved.content_hash}, downloaded {got}"
        )
    manifest = load_manifest(package_root)  # re-validate the tree we fetched
    package = manifest.package
    if (package.namespace, package.name, package.version) != (
        resolved.namespace,
        resolved.name,
        resolved.version,
    ):
        raise IntegrityError(
            f"downloaded manifest for {package_id} declares"
            f" {package.namespace}/{package.name}@{package.version}"
        )
    return Staged(resolved, package_root, manifest)


def stage_downloads(plan: RemotePlan) -> tuple[Path, list[Staged]]:
    """Fetch and verify every to-install blob into a fresh staging root
    (returned for the caller to clean up). Touches nothing under
    ``~/.scripticus`` — a failure here aborts before any mutation (D17).
    """
    staging_root = Path(tempfile.mkdtemp(prefix="scripticus-remote-"))
    staged: list[Staged] = []
    try:
        for action in plan.actions:
            staged.append(_stage_one(plan.remote, plan.token, action.resolved, staging_root))
    except Exception:
        shutil.rmtree(staging_root, ignore_errors=True)
        raise
    return staging_root, staged


# --- Commit ----------------------------------------------------------------


def apply_remote(plan: RemotePlan, staged: list[Staged], home: Path) -> None:
    """Commit the staged closure: unpack each package (dependencies first),
    write its shims, and record the whole closure in the lockfile with
    remote provenance and direct/transitive marking (D42/D10). Assumes any
    required tools were installed first (tools-first, D44).
    """
    bin_dir = home / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)

    lock = read_lockfile(home)
    provenance = {"type": "remote", "remote": plan.remote.name, "url": plan.remote.url}
    staged_by_id = {(s.resolved.namespace, s.resolved.name): s for s in staged}

    for resolved in plan.result.packages:  # dependency order (deps first)
        key = (resolved.namespace, resolved.name)
        existing = _find_entry(lock, *key)
        if resolved.already_satisfied:
            # Not re-fetched; only promote a transitive entry the user has now
            # asked for directly (never demote a direct install).
            if existing is not None and resolved.direct and not existing.get("direct"):
                existing["direct"] = True
            continue
        entity = staged_by_id[key]
        install_into_lock(
            home,
            bin_dir,
            entity.package_root,
            entity.manifest.package.language,
            resolved.namespace,
            resolved.name,
            resolved.version,
            resolved.content_hash,
            dict(resolved.commands),
            direct=resolved.direct or bool(existing and existing.get("direct")),
            provenance=provenance,
            dependencies=dict(entity.manifest.dependencies.packages),
            lock=lock,
        )

    write_lockfile(home, lock)
