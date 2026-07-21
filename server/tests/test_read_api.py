import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from scripticus_server import db
from scripticus_server.app import app, get_session


@pytest.fixture
def client(session_factory):
    def override():
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override
    yield TestClient(app)
    app.dependency_overrides.clear()


def add_package(session_factory, namespace, name, versions):
    """Seed a package. Each version is (version, kwargs) where kwargs may
    set yanked/description, an ``artifacts`` list of (platforms, language),
    and a ``commands`` list of command names.
    """
    with session_factory() as session:
        existing = session.scalar(
            select(db.Namespace).where(db.Namespace.name == namespace)
        )
        package = db.Package(
            namespace=existing or db.Namespace(name=namespace), name=name
        )
        for version, kwargs in versions:
            artifacts = kwargs.pop("artifacts", [])
            commands = kwargs.pop("commands", [])
            package_version = db.PackageVersion(
                package=package, version=version, **kwargs
            )
            for platforms, language in artifacts:
                db.Artifact(
                    package_version=package_version,
                    platforms=",".join(platforms),
                    language=language,
                )
            for command in commands:
                package_version.commands.append(
                    db.Command(name=command, script_path="main.sh")
                )
        session.add(package)
        session.commit()


def test_unknown_package_is_404(client):
    response = client.get("/packages/kevin-c/no-such-tool")
    assert response.status_code == 404
    assert "kevin-c/no-such-tool" in response.json()["detail"]


def test_versions_listed_newest_first_with_yanked_marked(client, session_factory):
    add_package(
        session_factory,
        "kevin-c",
        "my-tool",
        [
            ("1.0.0", {}),
            ("2.0.0-rc.1", {}),
            ("2.0.0", {"yanked": True}),
            ("1.5.0", {}),
        ],
    )
    response = client.get("/packages/kevin-c/my-tool")
    assert response.status_code == 200
    assert response.json()["versions"] == [
        {"version": "2.0.0", "yanked": True},
        {"version": "2.0.0-rc.1", "yanked": False},
        {"version": "1.5.0", "yanked": False},
        {"version": "1.0.0", "yanked": False},
    ]


def test_description_comes_from_latest_non_yanked_version(client, session_factory):
    add_package(
        session_factory,
        "kevin-c",
        "my-tool",
        [
            ("1.0.0", {"description": "old"}),
            ("1.1.0", {"description": "current"}),
            ("2.0.0", {"description": "broken", "yanked": True}),
        ],
    )
    body = client.get("/packages/kevin-c/my-tool").json()
    assert body["description"] == "current"


def test_search_matches_name_substring(client, session_factory):
    add_package(session_factory, "kevin-c", "my-tool", [("1.0.0", {})])
    add_package(session_factory, "kevin-c", "other", [("1.0.0", {})])
    results = client.get("/search", params={"q": "tool"}).json()["results"]
    assert [r["name"] for r in results] == ["my-tool"]


def test_search_matches_description(client, session_factory):
    add_package(
        session_factory, "kevin-c", "rotate", [("1.0.0", {"description": "prune backups"})]
    )
    add_package(session_factory, "kevin-c", "other", [("1.0.0", {"description": "nope"})])
    results = client.get("/search", params={"q": "backup"}).json()["results"]
    assert [r["name"] for r in results] == ["rotate"]


def test_search_matches_command_name(client, session_factory):
    add_package(
        session_factory, "kevin-c", "toolbox", [("1.0.0", {"commands": ["db-restore"]})]
    )
    add_package(session_factory, "kevin-c", "other", [("1.0.0", {"commands": ["ls"]})])
    results = client.get("/search", params={"q": "restore"}).json()["results"]
    assert [r["name"] for r in results] == ["toolbox"]


def test_search_is_case_insensitive(client, session_factory):
    add_package(
        session_factory, "kevin-c", "widget", [("1.0.0", {"description": "A Backup Tool"})]
    )
    results = client.get("/search", params={"q": "BACKUP"}).json()["results"]
    assert [r["name"] for r in results] == ["widget"]


def test_search_ignores_yanked_versions_for_content_match(client, session_factory):
    # The only version mentioning "backup" is yanked, so it must not surface.
    add_package(
        session_factory,
        "kevin-c",
        "gone",
        [("1.0.0", {"description": "backup", "yanked": True})],
    )
    assert client.get("/search", params={"q": "backup"}).json()["results"] == []


def test_search_with_empty_query_lists_everything(client, session_factory):
    add_package(session_factory, "kevin-c", "my-tool", [("1.0.0", {})])
    add_package(session_factory, "aaa", "zzz", [("1.0.0", {})])
    results = client.get("/search").json()["results"]
    assert [(r["namespace"], r["name"]) for r in results] == [
        ("aaa", "zzz"),
        ("kevin-c", "my-tool"),
    ]


def test_search_reports_latest_non_yanked_version(client, session_factory):
    add_package(
        session_factory,
        "kevin-c",
        "my-tool",
        [("1.0.0", {}), ("2.0.0", {"yanked": True})],
    )
    results = client.get("/search", params={"q": "my-tool"}).json()["results"]
    assert results[0]["latest_version"] == "1.0.0"


def test_search_omits_packages_with_every_version_yanked(client, session_factory):
    add_package(session_factory, "kevin-c", "my-tool", [("1.0.0", {"yanked": True})])
    assert client.get("/search").json()["results"] == []


def test_search_filters_by_platform(client, session_factory):
    add_package(
        session_factory,
        "kevin-c",
        "posix-tool",
        [("1.0.0", {"artifacts": [(["linux", "macos"], "bash")]})],
    )
    add_package(
        session_factory,
        "kevin-c",
        "windows-tool",
        [("1.0.0", {"artifacts": [(["windows"], "powershell")]})],
    )
    results = client.get("/search", params={"platform": "windows"}).json()["results"]
    assert [r["name"] for r in results] == ["windows-tool"]


def test_search_filters_by_language(client, session_factory):
    add_package(
        session_factory,
        "kevin-c",
        "posix-tool",
        [("1.0.0", {"artifacts": [(["linux"], "bash")]})],
    )
    add_package(
        session_factory,
        "kevin-c",
        "py-tool",
        [("1.0.0", {"artifacts": [(["linux"], "python")]})],
    )
    results = client.get("/search", params={"language": "python"}).json()["results"]
    assert [r["name"] for r in results] == ["py-tool"]


def test_search_platform_filter_excludes_versions_without_artifacts(
    client, session_factory
):
    add_package(session_factory, "kevin-c", "bare", [("1.0.0", {})])
    assert client.get("/search", params={"platform": "linux"}).json()["results"] == []
