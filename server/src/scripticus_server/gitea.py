"""The server's boundary to Gitea (D2): permission checks and blob storage.

Auth is pass-through (D32): every publish carries the caller's own Gitea
token, and this client acts with exactly that token's rights — the index
service holds no credentials and caches nothing ACL-shaped (D24). The
narrow surface here exists so tests can fake Gitea; nothing outside this
module speaks HTTP to it.
"""

import os

import httpx
from fastapi import Header, HTTPException

DEFAULT_GITEA_URL = "http://localhost:3000"


def gitea_url() -> str:
    return os.environ.get("SCRIPTICUS_GITEA_URL", DEFAULT_GITEA_URL)


class GiteaAuthError(Exception):
    """The token is missing, invalid, or expired."""


class GiteaError(Exception):
    """Gitea is unreachable or answered something unexpected."""


class GiteaClient:
    def __init__(self, base_url: str, token: str):
        self._client = httpx.Client(
            base_url=base_url,
            headers={"Authorization": f"token {token}"},
            timeout=30,
        )

    def _get(self, path: str) -> httpx.Response:
        try:
            return self._client.get(path)
        except httpx.HTTPError as exc:
            raise GiteaError(f"cannot reach Gitea: {exc}") from exc

    def authenticated_user(self) -> str:
        """The login of the token's owner; raises GiteaAuthError on a bad token."""
        response = self._get("/api/v1/user")
        if response.status_code in (401, 403):
            raise GiteaAuthError("Gitea rejected the token")
        if response.status_code != 200:
            raise GiteaError(f"unexpected {response.status_code} from Gitea /user")
        return response.json()["login"]

    def can_publish(self, namespace: str, user: str) -> bool:
        """Live ACL check (D24): the namespace is the user themselves, or an
        organisation the user is a member of. Unknown namespaces are simply
        not publishable — Gitea owns namespace existence.
        """
        if namespace == user:
            return True
        response = self._get(f"/api/v1/orgs/{namespace}/members/{user}")
        if response.status_code == 204:
            return True
        if response.status_code < 500:
            # Non-membership, nonexistent org, and permission refusals all
            # come back as assorted 3xx/4xx depending on Gitea version; every
            # one of them means "cannot publish here".
            return False
        raise GiteaError(f"unexpected {response.status_code} from Gitea org check")

    def _blob_path(self, namespace: str, name: str, version: str, filename: str) -> str:
        return f"/api/packages/{namespace}/generic/{name}/{version}/{filename}"

    def upload_blob(
        self, namespace: str, name: str, version: str, filename: str, data: bytes
    ) -> str:
        """Write the archive to Gitea's generic package registry; returns the
        download path (relative to the Gitea base URL) as the artifact pointer.
        """
        path = self._blob_path(namespace, name, version, filename)
        try:
            response = self._client.put(path, content=data)
        except httpx.HTTPError as exc:
            raise GiteaError(f"cannot reach Gitea: {exc}") from exc
        if response.status_code == 201:
            return path
        if response.status_code in (401, 403):
            raise GiteaAuthError("Gitea refused the blob write")
        raise GiteaError(
            f"Gitea blob write failed with {response.status_code}: {response.text}"
        )

    def delete_blob(
        self, namespace: str, name: str, version: str, filename: str
    ) -> None:
        """Best-effort cleanup of an uploaded blob after a failed publish;
        never raises — the publish error it accompanies matters more.
        """
        try:
            self._client.delete(self._blob_path(namespace, name, version, filename))
        except httpx.HTTPError:
            pass


def get_gitea_client(authorization: str | None = Header(None)) -> GiteaClient:
    """FastAPI dependency: a GiteaClient acting as the request's caller.

    Accepts ``Authorization: token <t>`` (Gitea's own convention) or
    ``Bearer <t>``.
    """
    if authorization is None:
        raise HTTPException(401, "publishing requires a Gitea token")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() not in ("token", "bearer") or not token:
        raise HTTPException(401, "malformed Authorization header")
    return GiteaClient(gitea_url(), token)
