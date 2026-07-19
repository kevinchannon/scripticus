from fastapi.testclient import TestClient

from scripticus_server import __version__
from scripticus_server.app import app

client = TestClient(app)


def test_version_returns_200_with_package_version():
    response = client.get("/version")
    assert response.status_code == 200
    assert response.json() == {"version": __version__}


def test_version_appears_in_generated_openapi_spec():
    response = client.get("/openapi.json")
    assert response.status_code == 200
    assert "/version" in response.json()["paths"]
