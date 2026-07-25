"""Publishing and finding snippet packages (D58): the derived-variant index
projection and what it makes searchable."""

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


def test_publish_projects_the_derived_variants(
    client, session_factory, make_snippet_archive_factory
):
    response = publish(client, make_snippet_archive_factory())
    assert response.status_code == 201, response.text

    with session_factory() as session:
        version = session.scalar(select(db.PackageVersion))
        [snippet] = version.snippets
        assert snippet.name == "args"
        assert snippet.description == "args boilerplate"
        # Never authored: read off src/args.py + src/args.sh at publish (D21).
        assert snippet.extension_list() == ["py", "sh"]
        assert version.commands == []


def test_a_snippet_package_carries_the_any_platform_and_snippet_language(
    client, session_factory, make_snippet_archive_factory
):
    publish(client, make_snippet_archive_factory())

    with session_factory() as session:
        [artifact] = session.scalars(select(db.Artifact)).all()
        assert artifact.language == "snippet"
        # Expanded, so every platform query matches without an `any` case.
        assert artifact.platform_list() == ["linux", "macos"]


def test_search_matches_snippet_names_and_descriptions(
    client, make_snippet_archive_factory
):
    publish(client, make_snippet_archive_factory(name="boilerplate"))

    # A snippet package's content is its snippets — searching for what you
    # want to paste has to find it, not just the package's own name.
    results = client.get("/search", params={"q": "args"}).json()["results"]
    assert [r["name"] for r in results] == ["boilerplate"]
    assert results[0]["kind"] == "snippet"
    assert results[0]["snippets"] == ["args.py", "args.sh"]


def test_language_filter_matches_a_variant_extension(
    client, make_snippet_archive_factory
):
    publish(client, make_snippet_archive_factory(snippets=("args.cpp",)))

    # The extension is the label (D58) — including for languages Scripticus
    # cannot run as commands.
    assert client.get("/search", params={"language": "cpp"}).json()["results"]
    assert not client.get("/search", params={"language": "rs"}).json()["results"]


def test_language_name_reaches_its_extension(client, make_snippet_archive_factory):
    publish(client, make_snippet_archive_factory(snippets=("args.py",)))

    # `--language python` must find args.py, or the filter would mean two
    # different things for commands and snippets.
    assert client.get("/search", params={"language": "python"}).json()["results"]
    assert not client.get("/search", params={"language": "bash"}).json()["results"]


def test_command_packages_are_unaffected_by_the_language_filter(client, make_archive):
    publish(client, make_archive(language="bash"))

    assert client.get("/search", params={"language": "bash"}).json()["results"]
    assert not client.get("/search", params={"language": "python"}).json()["results"]


def test_listing_reports_the_package_kind(client, make_snippet_archive_factory, make_archive):
    publish(client, make_snippet_archive_factory(name="boilerplate"))
    publish(client, make_archive(name="my-tool"))

    kinds = {r["name"]: r["kind"] for r in client.get("/packages").json()["results"]}
    assert kinds == {"boilerplate": "snippet", "my-tool": "command"}
