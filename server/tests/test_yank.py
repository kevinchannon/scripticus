import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from scripticus_server import db
from scripticus_server.app import app
from scripticus_server.db import get_session
from scripticus_server.gitea import get_gitea_client


@pytest.fixture
def client(session_factory, fake_gitea):
    def session_override():
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[get_gitea_client] = lambda: fake_gitea
    yield TestClient(app)
    app.dependency_overrides.clear()


def seed_version(session_factory, namespace, name, version, *, yanked=False):
    """Put one version in the index directly — yank needs no artifacts to
    flip, so this stays minimal (unlike a real publish)."""
    with session_factory() as session:
        existing = session.scalar(
            select(db.Namespace).where(db.Namespace.name == namespace)
        )
        package = db.Package(
            namespace=existing or db.Namespace(name=namespace), name=name
        )
        db.PackageVersion(package=package, version=version, yanked=yanked)
        session.add(package)
        session.commit()


def yanked_state(session_factory, namespace, name, version):
    with session_factory() as session:
        return session.scalar(
            select(db.PackageVersion.yanked)
            .join(db.Package)
            .join(db.Namespace)
            .where(
                db.Namespace.name == namespace,
                db.Package.name == name,
                db.PackageVersion.version == version,
            )
        )


def patch_yank(client, namespace, name, version, yanked):
    return client.patch(
        f"/packages/{namespace}/{name}/{version}", json={"yanked": yanked}
    )


def test_yank_sets_the_flag(client, session_factory):
    seed_version(session_factory, "kevin-c", "my-tool", "1.0.0")
    response = patch_yank(client, "kevin-c", "my-tool", "1.0.0", True)
    assert response.status_code == 200, response.text
    assert response.json() == {
        "namespace": "kevin-c",
        "name": "my-tool",
        "version": "1.0.0",
        "yanked": True,
    }
    assert yanked_state(session_factory, "kevin-c", "my-tool", "1.0.0") is True


def test_undo_clears_the_flag(client, session_factory):
    seed_version(session_factory, "kevin-c", "my-tool", "1.0.0", yanked=True)
    response = patch_yank(client, "kevin-c", "my-tool", "1.0.0", False)
    assert response.status_code == 200, response.text
    assert response.json()["yanked"] is False
    assert yanked_state(session_factory, "kevin-c", "my-tool", "1.0.0") is False


def test_yank_is_reflected_in_the_read_api(client, session_factory):
    seed_version(session_factory, "kevin-c", "my-tool", "1.0.0")
    patch_yank(client, "kevin-c", "my-tool", "1.0.0", True)

    listing = client.get("/packages/kevin-c/my-tool").json()
    assert listing["versions"] == [{"version": "1.0.0", "yanked": True}]
    # A fully-yanked package drops out of search entirely (npm-style).
    assert client.get("/search", params={"q": "my-tool"}).json()["results"] == []


def test_yank_is_idempotent(client, session_factory):
    seed_version(session_factory, "kevin-c", "my-tool", "1.0.0", yanked=True)
    response = patch_yank(client, "kevin-c", "my-tool", "1.0.0", True)
    assert response.status_code == 200
    assert response.json()["yanked"] is True


def test_unknown_version_is_404(client, session_factory):
    seed_version(session_factory, "kevin-c", "my-tool", "1.0.0")
    response = patch_yank(client, "kevin-c", "my-tool", "2.0.0", True)
    assert response.status_code == 404
    assert "kevin-c/my-tool" in response.json()["detail"]


def test_unknown_package_is_404(client):
    response = patch_yank(client, "kevin-c", "no-such-tool", "1.0.0", True)
    assert response.status_code == 404


def test_yanking_anothers_namespace_is_403(client, session_factory):
    seed_version(session_factory, "somebody-else", "my-tool", "1.0.0")
    response = patch_yank(client, "somebody-else", "my-tool", "1.0.0", True)
    assert response.status_code == 403
    assert "somebody-else" in response.json()["detail"]
    # A refused caller changes nothing.
    assert yanked_state(session_factory, "somebody-else", "my-tool", "1.0.0") is False


def test_org_member_may_yank_org_namespace(client, fake_gitea, session_factory):
    fake_gitea.orgs.add("acme")
    seed_version(session_factory, "acme", "my-tool", "1.0.0")
    response = patch_yank(client, "acme", "my-tool", "1.0.0", True)
    assert response.status_code == 200
    assert yanked_state(session_factory, "acme", "my-tool", "1.0.0") is True


def test_bad_token_is_401(client, fake_gitea, session_factory):
    fake_gitea.bad_token = True
    seed_version(session_factory, "kevin-c", "my-tool", "1.0.0")
    response = patch_yank(client, "kevin-c", "my-tool", "1.0.0", True)
    assert response.status_code == 401
    # Auth is checked before the lookup, so nothing was mutated.
    assert yanked_state(session_factory, "kevin-c", "my-tool", "1.0.0") is False
