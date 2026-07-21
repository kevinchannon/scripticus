"""Publishing pre-built archives to a remote (`scripticus publish`, D36/D37).

`publish` never invokes `pack`: it takes a path whose last component is a
``<name>-<version>`` prefix and matches every archive in that directory
whose D26 wheel-style filename carries exactly those name and version
fields. Matching is structural, not ``startswith()`` — the filename's
dash-separated fields are parsed and compared with dash/underscore
normalised on both sides, so ``my-cool-script-0.1.2`` matches
``my_cool_script-0.1.2-...`` but never ``...-0.1.20-...``.

Every matched archive goes up in one multipart request (D37); the server
publishes the whole batch or rejects it, so the client reports exactly
one outcome. Auth is the stored (or ``SCRIPTICUS_TOKEN``) Gitea token,
replayed in the ``Authorization`` header — D32's pass-through.
"""

from pathlib import Path

import httpx

from scripticus.config import Remote, find_remote
from scripticus_schema.manifest import FORMAT_GROUPS
from scripticus_schema.publish_api import PublishResult


class PublishError(Exception):
    """Nothing was published."""


_EXTENSIONS = tuple(f".{extension}" for extension, _ in FORMAT_GROUPS)


def _name_version_of(filename: str) -> str | None:
    """The ``name-version`` of a D26 archive filename, dash-normalised for
    comparison; ``None`` when the filename isn't shaped like an archive.
    """
    for extension in _EXTENSIONS:
        if filename.endswith(extension):
            stem = filename[: -len(extension)]
            break
    else:
        return None
    # D26: name-version-platformtag-language, where dashes inside name and
    # version were normalised to underscores — exactly four fields.
    fields = stem.split("-")
    if len(fields) != 4 or not all(fields):
        return None
    name, version = fields[0], fields[1]
    return f"{name}-{version}".replace("_", "-")


def matching_archives(path_prefix: Path) -> list[Path]:
    """Every archive next to ``path_prefix`` whose filename's name/version
    fields match its last component, in deterministic (sorted) order.
    """
    directory = path_prefix.parent
    if not directory.is_dir():
        raise PublishError(f"no such directory: {directory}")

    wanted = path_prefix.name.replace("_", "-")
    matches = [
        entry
        for entry in sorted(directory.iterdir())
        if entry.is_file() and _name_version_of(entry.name) == wanted
    ]
    if not matches:
        raise PublishError(
            f"no archives matching '{path_prefix.name}' in {directory}"
            " — run 'scripticus pack' first?"
        )
    return matches


def resolve_remote(name: str | None, remotes: list[Remote]) -> Remote:
    """``--remote <name>``, or the first configured remote (D35)."""
    if not remotes:
        raise PublishError(
            "no remotes configured — run 'scripticus login <name> <url>' first"
        )
    if name is None:
        return remotes[0]
    remote = find_remote(remotes, name)
    if remote is None:
        known = ", ".join(r.name for r in remotes)
        raise PublishError(f"no remote named '{name}' (remotes: {known})")
    return remote


def _client() -> httpx.Client:
    # Seam for tests: monkeypatched with an httpx.MockTransport-backed client.
    return httpx.Client(timeout=30.0)


def publish_archives(
    remote: Remote, token: str, archives: list[Path]
) -> PublishResult:
    """POST the whole batch to ``remote``; one outcome, never a subset (D37)."""
    files = [
        ("archives", (path.name, path.read_bytes(), "application/octet-stream"))
        for path in archives
    ]
    try:
        with _client() as client:
            response = client.post(
                remote.url.rstrip("/") + "/packages",
                files=files,
                headers={"Authorization": f"token {token}"},
            )
    except httpx.HTTPError as exc:
        raise PublishError(f"cannot reach '{remote.name}' ({remote.url}): {exc}") from exc

    if response.status_code == 401:
        raise PublishError(
            f"'{remote.name}' rejected the token"
            f" — run 'scripticus login {remote.name}' with a fresh Gitea token"
        )
    if response.status_code != 201:
        try:
            detail = response.json().get("detail", response.text)
        except ValueError:
            detail = response.text
        raise PublishError(
            f"publish to '{remote.name}' failed ({response.status_code}): {detail}"
        )
    return PublishResult.model_validate(response.json())
