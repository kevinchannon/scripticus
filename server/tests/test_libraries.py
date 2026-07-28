"""Publishing and finding library packages (D57): the index projection that
records a version is a library, and how a command-less package surfaces."""

from contextlib import ExitStack

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


def publish(client, archive):
    with ExitStack() as stack:
        files = [("archives", (archive.name, stack.enter_context(archive.open("rb"))))]
        return client.post("/packages", files=files)


def test_publish_records_the_library_marker(
    client, session_factory, make_library_archive_factory
):
    response = publish(client, make_library_archive_factory())
    assert response.status_code == 201, response.text

    with session_factory() as session:
        version = session.scalar(select(db.PackageVersion))
        [library] = version.libraries
        assert library.entrypoint == "src/load.sh"
        # A library provides nothing runnable, so it contributes no command
        # rows — which is exactly what keeps it out of the shim system.
        assert version.commands == []
        assert version.snippets == []


def test_a_library_keeps_its_real_language_and_platforms(
    client, session_factory, make_library_archive_factory
):
    # Unlike a snippet, a library is real shell code for real platforms, so no
    # sentinel language and no `any` tag.
    publish(client, make_library_archive_factory(language="sh", os_list=("linux",)))

    with session_factory() as session:
        [artifact] = session.scalars(select(db.Artifact)).all()
        assert artifact.language == "sh"
        assert artifact.platform_list() == ["linux"]


def test_a_command_package_records_no_library_row(
    client, session_factory, make_archive
):
    publish(client, make_archive())

    with session_factory() as session:
        version = session.scalar(select(db.PackageVersion))
        assert version.libraries == []


def test_listing_reports_the_library_kind(
    client, make_library_archive_factory, make_archive
):
    publish(client, make_library_archive_factory(name="strings"))
    publish(client, make_archive(name="my-tool"))

    kinds = {r["name"]: r["kind"] for r in client.get("/packages").json()["results"]}
    assert kinds == {"strings": "library", "my-tool": "command"}


def test_search_finds_a_library_by_name_and_description(
    client, make_library_archive_factory
):
    # A library has no command or snippet names to match, so identity and
    # description are the whole of its searchable content (D57).
    publish(
        client,
        make_library_archive_factory(name="strings", description="String helpers"),
    )

    by_name = client.get("/search", params={"q": "string"}).json()["results"]
    assert [r["name"] for r in by_name] == ["strings"]
    assert by_name[0]["kind"] == "library"
    assert by_name[0]["snippets"] == []

    by_description = client.get("/search", params={"q": "helpers"}).json()["results"]
    assert [r["name"] for r in by_description] == ["strings"]


def test_a_library_answers_to_its_language_filter(client, make_library_archive_factory):
    publish(client, make_library_archive_factory(language="sh"))

    assert client.get("/search", params={"language": "sh"}).json()["results"]
    assert not client.get("/search", params={"language": "python"}).json()["results"]
