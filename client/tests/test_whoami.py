import httpx
import pytest

import scripticus.whoami as whoami_module
from scripticus.config import Remote
from scripticus.whoami import WhoAmIError, verify_token

REMOTE = Remote(name="origin", url="https://scripts.example.com")


def fake_server(monkeypatch, handler) -> list[httpx.Request]:
    requests: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request)

    transport = httpx.MockTransport(record)
    monkeypatch.setattr(
        whoami_module, "_client", lambda: httpx.Client(transport=transport)
    )
    return requests


def test_verify_returns_the_authenticated_identity(monkeypatch):
    requests = fake_server(
        monkeypatch, lambda request: httpx.Response(200, json={"username": "kevin-c"})
    )
    identity = verify_token(REMOTE, "tok-123")
    assert identity.username == "kevin-c"

    (request,) = requests
    assert request.url == "https://scripts.example.com/whoami"
    assert request.headers["Authorization"] == "token tok-123"


def test_trailing_slash_in_url_is_not_doubled(monkeypatch):
    requests = fake_server(
        monkeypatch, lambda request: httpx.Response(200, json={"username": "kevin-c"})
    )
    verify_token(Remote(name="origin", url="https://scripts.example.com/"), "tok")
    assert requests[0].url == "https://scripts.example.com/whoami"


def test_401_raises_a_bad_token_error(monkeypatch):
    fake_server(monkeypatch, lambda request: httpx.Response(401, json={"detail": "no"}))
    with pytest.raises(WhoAmIError, match="rejected the token"):
        verify_token(REMOTE, "wrong")


def test_unreachable_remote_raises_a_distinct_error(monkeypatch):
    def unreachable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    fake_server(monkeypatch, unreachable)
    with pytest.raises(WhoAmIError, match="could not reach 'origin'"):
        verify_token(REMOTE, "tok")


def test_unexpected_status_surfaces_the_detail(monkeypatch):
    fake_server(
        monkeypatch, lambda request: httpx.Response(502, json={"detail": "gitea down"})
    )
    with pytest.raises(WhoAmIError, match="gitea down"):
        verify_token(REMOTE, "tok")
