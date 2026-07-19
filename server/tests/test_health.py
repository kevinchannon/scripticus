from fastapi.testclient import TestClient

from scripticus_server.app import app

client = TestClient(app)


def test_health_returns_200_with_ok_status():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_appears_in_generated_openapi_spec():
    response = client.get("/openapi.json")
    assert response.status_code == 200
    assert "/health" in response.json()["paths"]
