"""Yanking a published version (`scripticus yank`, D54).

Yank hides a broken version from search and `latest`/range resolution while
leaving it fetchable by anything that pins it exactly (D16) — nothing is
deleted. It is whole-version (D23), so the target must name an *exact* version,
not a range: ``namespace/name@1.2.0``. ``--undo`` reverses a yank (un-yank) —
the same PATCH with the flag cleared, available with no time window because the
yank destroyed nothing. Auth is the stored (or ``SCRIPTICUS_TOKEN``) Gitea
token replayed in the ``Authorization`` header, owner-checked server-side
exactly as publish is (D32).
"""

import httpx

from scripticus.config import Remote, find_remote
from scripticus_common.semver import SEMVER_RE
from scripticus_schema.yank_api import YankRequest, YankResult


class YankError(Exception):
    """The version's yank state was not changed."""


def parse_target(target: str) -> tuple[str, str, str]:
    """Split ``namespace/name@version`` into ``(namespace, name, version)``.

    v1 requires the fully-namespaced form (D46) and an *exact* semver: yank is
    whole-version (D23), so a range or partial version is rejected — there is
    nothing to resolve, the caller names the one version to hide.
    """
    root, sep, version = target.partition("@")
    if root.count("/") != 1 or not all(root.split("/")):
        raise YankError(
            f"'{target}' is not a fully-namespaced package name"
            " — yank needs 'namespace/name@version'"
        )
    if not sep or not version:
        raise YankError(
            f"'{target}' is missing a version — yank needs an exact"
            " 'namespace/name@version' (whole-version, no ranges)"
        )
    if not SEMVER_RE.match(version):
        raise YankError(
            f"'{version}' is not an exact version — yank takes a single"
            " published version, not a range or partial version"
        )
    namespace, _, name = root.partition("/")
    return namespace, name, version


def resolve_remote(name: str | None, remotes: list[Remote]) -> Remote:
    """``--remote <name>``, or the first configured remote — as publish (D35)."""
    if not remotes:
        raise YankError(
            "no remotes configured — run 'scripticus login <name> <url>' first"
        )
    if name is None:
        return remotes[0]
    remote = find_remote(remotes, name)
    if remote is None:
        known = ", ".join(r.name for r in remotes)
        raise YankError(f"no remote named '{name}' (remotes: {known})")
    return remote


def _client() -> httpx.Client:
    # Seam for tests: monkeypatched with an httpx.MockTransport-backed client.
    return httpx.Client(timeout=30.0)


def yank_version(
    remote: Remote,
    token: str,
    namespace: str,
    name: str,
    version: str,
    *,
    undo: bool,
) -> YankResult:
    """PATCH the version's yanked flag on ``remote``; ``undo`` clears it."""
    url = f"{remote.url.rstrip('/')}/packages/{namespace}/{name}/{version}"
    try:
        with _client() as client:
            response = client.patch(
                url,
                json=YankRequest(yanked=not undo).model_dump(),
                headers={"Authorization": f"token {token}"},
            )
    except httpx.HTTPError as exc:
        raise YankError(f"cannot reach '{remote.name}' ({remote.url}): {exc}") from exc

    if response.status_code == 401:
        raise YankError(
            f"'{remote.name}' rejected the token"
            f" — run 'scripticus login {remote.name}' with a fresh Gitea token"
        )
    if response.status_code != 200:
        try:
            detail = response.json().get("detail", response.text)
        except ValueError:
            detail = response.text
        verb = "un-yank" if undo else "yank"
        raise YankError(
            f"{verb} of {namespace}/{name}@{version} on '{remote.name}'"
            f" failed ({response.status_code}): {detail}"
        )
    return YankResult.model_validate(response.json())
