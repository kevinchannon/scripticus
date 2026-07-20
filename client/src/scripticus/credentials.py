"""The credential store: one Gitea token per remote, cargo-style (D34).

``~/.scripticus/credentials.toml`` maps a remote's index-service URL to a
Gitea personal access token, plaintext with 0600 permissions — the
cargo/npm/docker precedent, honest about the threat model. Keyed by URL,
not remote name, so renaming a remote in ``config.toml`` doesn't orphan
its token. A separate file from ``config.toml`` because that file is
org-distributable (D12): tokens must not travel with it.

``SCRIPTICUS_TOKEN``, when set, overrides any stored token — the CI path.
"""

import os
import tomllib
from pathlib import Path

from scripticus.config import Remote


class CredentialsError(Exception):
    """No usable token for a remote."""


def _credentials_path(home: Path) -> Path:
    return home / "credentials.toml"


def _toml_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def load_credentials(home: Path) -> dict[str, str]:
    """URL -> token. No credentials file yet is an empty store."""
    path = _credentials_path(home)
    if not path.is_file():
        return {}
    return dict(tomllib.loads(path.read_text()))


def set_token(home: Path, url: str, token: str) -> None:
    """Store (or replace) the token for ``url``, keeping the file 0600.

    Permissions are set at creation and re-asserted after writing, since
    a pre-existing file keeps whatever mode it already had.
    """
    credentials = load_credentials(home)
    credentials[url] = token

    home.mkdir(parents=True, exist_ok=True)
    path = _credentials_path(home)
    lines = [f"{_toml_string(u)} = {_toml_string(t)}" for u, t in credentials.items()]
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as file:
        file.write("\n".join(lines) + "\n")
    os.chmod(path, 0o600)


def resolve_token(remote: Remote, home: Path, environ=os.environ) -> str:
    """The token publish should send to ``remote``: ``SCRIPTICUS_TOKEN``
    first (D34's CI override), else the stored one.
    """
    env_token = environ.get("SCRIPTICUS_TOKEN")
    if env_token:
        return env_token
    token = load_credentials(home).get(remote.url)
    if token is None:
        raise CredentialsError(
            f"not logged in to '{remote.name}' — run 'scripticus login {remote.name}'"
        )
    return token
