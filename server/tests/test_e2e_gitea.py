"""End-to-end publish tests against a real Gitea instance.

Excluded from the default run (root pyproject deselects the e2e marker);
run with ``pytest -m e2e`` and these environment variables:

- ``SCRIPTICUS_E2E_GITEA_URL``   — base URL of a running Gitea
- ``SCRIPTICUS_E2E_GITEA_TOKEN`` — token with package write + user read scopes
- ``SCRIPTICUS_E2E_GITEA_USER``  — the token owner's login (the namespace)

The .github/workflows/e2e.yml job provisions all three.
"""

import os

import httpx
import pytest
from fastapi.testclient import TestClient

from scripticus_server.app import app
from scripticus_server.db import get_session

GITEA_URL = os.environ.get("SCRIPTICUS_E2E_GITEA_URL")
GITEA_TOKEN = os.environ.get("SCRIPTICUS_E2E_GITEA_TOKEN")
GITEA_USER = os.environ.get("SCRIPTICUS_E2E_GITEA_USER")

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        not (GITEA_URL and GITEA_TOKEN and GITEA_USER),
        reason="SCRIPTICUS_E2E_GITEA_* environment not configured",
    ),
]


@pytest.fixture
def client(session_factory, monkeypatch):
    monkeypatch.setenv("SCRIPTICUS_GITEA_URL", GITEA_URL)

    def session_override():
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = session_override
    yield TestClient(app)
    app.dependency_overrides.clear()


def post_archive(client, archive_path):
    with archive_path.open("rb") as f:
        return client.post(
            "/packages",
            files={"archives": (archive_path.name, f)},
            headers={"Authorization": f"token {GITEA_TOKEN}"},
        )


def test_publish_stores_blob_in_real_gitea(client, make_archive):
    archive = make_archive(namespace=GITEA_USER, name="e2e-tool")
    response = post_archive(client, archive)
    assert response.status_code == 201, response.text
    pointer = response.json()

    blob = httpx.get(
        GITEA_URL + f"/api/packages/{GITEA_USER}/generic/e2e-tool/1.0.0/{pointer['artifacts'][0]['filename']}",
        headers={"Authorization": f"token {GITEA_TOKEN}"},
    )
    assert blob.status_code == 200
    assert blob.content == archive.read_bytes()

    listing = client.get(f"/packages/{GITEA_USER}/e2e-tool")
    assert listing.json()["versions"] == [{"version": "1.0.0", "yanked": False}]


def test_unknown_namespace_is_403_with_real_gitea(client, make_archive):
    archive = make_archive(namespace="no-such-org", name="e2e-tool")
    response = post_archive(client, archive)
    assert response.status_code == 403


def test_bad_token_is_401_with_real_gitea(client, make_archive):
    archive = make_archive(namespace=GITEA_USER, name="e2e-tool-auth")
    with archive.open("rb") as f:
        response = client.post(
            "/packages",
            files={"archives": (archive.name, f)},
            headers={"Authorization": "token not-a-real-token"},
        )
    assert response.status_code == 401
