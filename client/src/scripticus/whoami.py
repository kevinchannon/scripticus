"""Verifying a Gitea token against a remote before storing it (D41).

`scripticus login` calls the remote's `GET /whoami` (D40) with the freshly
entered token before writing `credentials.toml`, so a mistyped token fails
at the prompt with a clear message instead of surfacing as a confusing
first-publish failure (D34). The call is pass-through auth exactly as
publish is (D32): the token rides in the ``Authorization`` header and the
remote echoes back the authenticated Gitea login.

A token that doesn't verify is never stored. A rejected token (401) and an
unreachable remote are distinct errors — the token may be perfectly good
and the remote simply down — but both refuse the login: storing an
unverified token would defeat the point of verifying at all.
"""

import httpx

from scripticus.config import Remote
from scripticus_schema.whoami_api import WhoAmI


class WhoAmIError(Exception):
    """The token could not be verified against the remote; do not store it."""


def _client() -> httpx.Client:
    # Seam for tests: monkeypatched with an httpx.MockTransport-backed client.
    return httpx.Client(timeout=30.0)


def verify_token(remote: Remote, token: str) -> WhoAmI:
    """Confirm ``token`` authenticates against ``remote``, returning the
    identity it authenticates as. Raises WhoAmIError on a rejected token or
    an unreachable remote — the caller must not store a token that this
    didn't return for.
    """
    try:
        with _client() as client:
            response = client.get(
                remote.url.rstrip("/") + "/whoami",
                headers={"Authorization": f"token {token}"},
            )
    except httpx.HTTPError as exc:
        raise WhoAmIError(
            f"could not reach '{remote.name}' ({remote.url}) to verify the token"
            f" — the token may be fine and the remote down; nothing was stored"
            f" ({exc})"
        ) from exc

    if response.status_code == 401:
        raise WhoAmIError(
            f"'{remote.name}' rejected the token — check it and try again;"
            " nothing was stored"
        )
    if response.status_code != 200:
        try:
            detail = response.json().get("detail", response.text)
        except ValueError:
            detail = response.text
        raise WhoAmIError(
            f"could not verify the token with '{remote.name}'"
            f" ({response.status_code}): {detail}"
        )
    return WhoAmI.model_validate(response.json())
