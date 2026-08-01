"""Publishing pre-built archives to a remote (`scripticus publish`, D36/D37).

`publish` never invokes `pack`: it takes a path whose last component is a
``<name>-<version>`` prefix and matches every archive in that directory
whose D26 wheel-style filename carries exactly those name and version
fields. Matching is structural, not ``startswith()`` — the filename's
dash-separated fields are parsed and compared with dash/underscore
normalised on both sides, so ``my-cool-script-0.1.2`` matches
``my_cool_script-0.1.2-...`` but never ``...-0.1.20-...``.

One of the archives themselves is accepted in place of the prefix (D65) and
means the same thing: its own name and version fields select the batch. That
is what tab-completion produces, and a version is what publish uploads either
way — so naming one variant publishes all of them, which the CLI says out loud
rather than leaving to be noticed in the result list.

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


def derived_prefix(path: Path) -> Path | None:
    """The ``<name>-<version>`` prefix ``path`` names, if it names an archive.

    Tab-completion hands you a whole filename, so that is what people type
    (D65). One archive names its version unambiguously — the name and version
    are two of the four fields — so accept it and publish the version, rather
    than rejecting the most natural input. ``None`` when ``path`` is not an
    archive filename, i.e. already a prefix.
    """
    name_version = _name_version_of(path.name)
    return None if name_version is None else path.with_name(name_version)


def _looks_like_an_archive(filename: str) -> bool:
    """Does ``filename`` end in an archive extension? (Shape aside.)"""
    return filename.endswith(_EXTENSIONS)


def matching_archives(path_prefix: Path) -> list[Path]:
    """Every archive next to ``path_prefix`` whose filename's name/version
    fields match its last component, in deterministic (sorted) order.

    ``path_prefix`` may also *be* one of those archives, in which case its own
    name and version select the batch (D65).
    """
    directory = path_prefix.parent
    if not directory.is_dir():
        raise PublishError(f"no such directory: {directory}")

    prefix = derived_prefix(path_prefix) or path_prefix
    wanted = prefix.name.replace("_", "-")
    matches = [
        entry
        for entry in sorted(directory.iterdir())
        if entry.is_file() and _name_version_of(entry.name) == wanted
    ]
    if not matches:
        # An argument that ends in an archive extension but did not parse is a
        # mistyped or hand-renamed filename, not a missing build — sending that
        # user to `pack` would send them to the one thing already done.
        if _looks_like_an_archive(path_prefix.name):
            raise PublishError(
                f"'{path_prefix.name}' is not a Scripticus archive filename"
                " (expected <name>-<version>-<platforms>-<language>.<ext>)"
                " — publish takes an archive or its <name>-<version> prefix"
            )
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
