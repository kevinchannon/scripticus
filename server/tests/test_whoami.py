import pytest
from fastapi.testclient import TestClient

from scripticus_server.app import app
from scripticus_server.gitea import GiteaError, get_gitea_client


@pytest.fixture
def client(fake_gitea):
    app.dependency_overrides[get_gitea_client] = lambda: fake_gitea
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_whoami_returns_authenticated_login(client, fake_gitea):
    fake_gitea.user = "kevin-c"
    response = client.get("/whoami")
    assert response.status_code == 200, response.text
    assert response.json() == {"username": "kevin-c"}


def test_whoami_bad_token_is_401(client, fake_gitea):
    fake_gitea.bad_token = True
    response = client.get("/whoami")
    assert response.status_code == 401
    assert "token" in response.json()["detail"].lower()


def test_whoami_missing_token_is_401():
    # No get_gitea_client override: the header dependency itself rejects the
    # request before any Gitea call.
    try:
        response = TestClient(app).get("/whoami")
        assert response.status_code == 401
    finally:
        app.dependency_overrides.clear()


def test_whoami_gitea_unreachable_is_502(client, fake_gitea):
    def unreachable():
        raise GiteaError("cannot reach Gitea")

    fake_gitea.authenticated_user = unreachable
    response = client.get("/whoami")
    assert response.status_code == 502
