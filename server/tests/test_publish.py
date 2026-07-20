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


def post_archives(client, *archive_paths):
    """Send every given archive as one part each under the batch's shared
    "archives" multipart field, in a single request (D37).
    """
    with ExitStack() as stack:
        files = [
            ("archives", (path.name, stack.enter_context(path.open("rb"))))
            for path in archive_paths
        ]
        return client.post("/packages", files=files)


def post_archive(client, archive_path):
    """One-archive convenience wrapper — a batch of size one (today's
    single-archive behaviour, unchanged outcome, D37).
    """
    return post_archives(client, archive_path)


def test_publish_records_version_and_uploads_blob(
    client, fake_gitea, session_factory, make_archive
):
    response = post_archive(client, make_archive(description="A demo tool"))
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["namespace"] == "kevin-c"
    assert body["name"] == "my-tool"
    assert body["version"] == "1.0.0"
    assert body["publisher"] == "kevin-c"
    assert body["content_hash"].startswith("sha256:")
    assert len(body["artifacts"]) == 1
    assert body["artifacts"][0]["archive_format"] == "tar.gz"
    assert body["artifacts"][0]["platforms"] == ["linux", "macos"]
    assert len(fake_gitea.uploads) == 1

    with session_factory() as session:
        version = session.scalar(select(db.PackageVersion))
        assert version.version == "1.0.0"
        assert version.description == "A demo tool"
        assert version.publisher == "kevin-c"
        assert version.manifest_blob.toml.strip().startswith("[package]")
        assert [c.name for c in version.commands] == ["my-tool"]
        assert version.artifacts[0].gitea_pointer == (
            "/api/packages/kevin-c/generic/my-tool/1.0.0/"
            + fake_gitea.uploads[0][3]
        )


def test_published_package_appears_in_read_api(client, make_archive):
    post_archive(client, make_archive())
    listing = client.get("/packages/kevin-c/my-tool")
    assert listing.status_code == 200
    assert listing.json()["versions"] == [{"version": "1.0.0", "yanked": False}]
    results = client.get("/search", params={"q": "my-tool"}).json()["results"]
    assert results[0]["latest_version"] == "1.0.0"


def test_missing_token_is_401(session_factory, make_archive):
    def session_override():
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = session_override
    try:
        client = TestClient(app)
        response = post_archive(client, make_archive())
        assert response.status_code == 401
    finally:
        app.dependency_overrides.clear()


def test_bad_token_is_401(fake_gitea, client, make_archive):
    fake_gitea.bad_token = True
    response = post_archive(client, make_archive())
    assert response.status_code == 401


def test_publishing_anothers_namespace_is_403(client, make_archive):
    response = post_archive(client, make_archive(namespace="somebody-else"))
    assert response.status_code == 403
    assert "somebody-else" in response.json()["detail"]


def test_org_member_may_publish_to_org_namespace(client, fake_gitea, make_archive):
    fake_gitea.orgs.add("acme")
    response = post_archive(client, make_archive(namespace="acme"))
    assert response.status_code == 201


def test_library_namespace_is_reserved(client, fake_gitea, make_archive):
    fake_gitea.user = "library"
    response = post_archive(client, make_archive(namespace="library"))
    assert response.status_code == 403
    assert "reserved" in response.json()["detail"]


def test_invalid_manifest_is_422(client, make_archive):
    response = post_archive(client, make_archive(version="not-semver"))
    assert response.status_code == 422
    assert "semver" in response.json()["detail"]


def test_unsupported_archive_extension_is_400(client, tmp_path):
    bogus = tmp_path / "package.rar"
    bogus.write_bytes(b"not an archive")
    with bogus.open("rb") as f:
        response = client.post("/packages", files={"archives": ("package.rar", f)})
    assert response.status_code == 400


def test_corrupt_archive_is_400(client, tmp_path):
    bogus = tmp_path / "package.tar.gz"
    bogus.write_bytes(b"this is not a tarball")
    with bogus.open("rb") as f:
        response = client.post("/packages", files={"archives": ("package.tar.gz", f)})
    assert response.status_code == 400


def test_empty_batch_is_400(client):
    response = client.post("/packages", files=[])
    assert response.status_code in (400, 422)


def test_format_mismatching_declared_platforms_is_422(client, make_archive):
    # A zip carries Windows targets; this manifest declares POSIX only.
    response = post_archive(
        client, make_archive(os_list=("linux",), archive_format="zip")
    )
    assert response.status_code == 422
    assert "platforms" in response.json()["detail"]


def test_duplicate_version_and_format_is_409(client, make_archive):
    post_archive(client, make_archive())
    response = post_archive(client, make_archive())
    assert response.status_code == 409
    assert "duplicate" in response.json()["detail"]


def test_new_format_variant_of_identical_tree_is_accepted(client, make_archive):
    # A *separate* publish adding a format to an already-published version
    # remains a batch of one, validated against already-committed index
    # state — explicitly unchanged by D37.
    first = make_archive(os_list=("linux", "windows"))
    second = make_archive(os_list=("linux", "windows"), archive_format="zip")
    assert post_archive(client, first).status_code == 201
    response = post_archive(client, second)
    assert response.status_code == 201, response.text
    assert response.json()["artifacts"][0]["platforms"] == ["windows"]

    listing = client.get("/packages/kevin-c/my-tool").json()
    assert listing["versions"] == [{"version": "1.0.0", "yanked": False}]


def test_same_version_with_different_content_is_409(client, make_archive):
    assert post_archive(client, make_archive()).status_code == 201
    changed = make_archive(
        os_list=("windows",), archive_format="zip", description="changed"
    )
    response = post_archive(client, changed)
    assert response.status_code == 409
    assert "immutable" in response.json()["detail"]


def test_dependency_must_be_fully_namespaced(client, make_archive):
    response = post_archive(client, make_archive(dependencies={"bare-name": "^1.0"}))
    assert response.status_code == 422
    assert "fully namespaced" in response.json()["detail"]


def test_dependency_must_already_exist_in_index(client, make_archive):
    response = post_archive(
        client, make_archive(dependencies={"kevin-c/not-published": "^1.0"})
    )
    assert response.status_code == 422
    assert "kevin-c/not-published" in response.json()["detail"]


def test_publish_with_existing_dependency_succeeds(client, make_archive):
    post_archive(client, make_archive(name="base-lib"))
    response = post_archive(
        client, make_archive(name="app", dependencies={"kevin-c/base-lib": "^1.0"})
    )
    assert response.status_code == 201, response.text


def test_dependency_cycle_is_rejected(client, make_archive):
    # a 1.0 (no deps), then b -> a, then a 2.0 -> b would close the cycle.
    post_archive(client, make_archive(name="a"))
    post_archive(client, make_archive(name="b", dependencies={"kevin-c/a": "^1.0"}))
    response = post_archive(
        client,
        make_archive(name="a", version="2.0.0", dependencies={"kevin-c/b": "^1.0"}),
    )
    assert response.status_code == 422
    assert "cycle" in response.json()["detail"]


def test_failed_blob_write_leaves_no_index_record(client, fake_gitea, make_archive):
    fake_gitea.fail_upload = True
    response = post_archive(client, make_archive())
    assert response.status_code == 502
    assert client.get("/packages/kevin-c/my-tool").status_code == 404


def test_failed_index_commit_deletes_uploaded_blob(
    session_factory, fake_gitea, make_archive
):
    def broken_commit():
        raise RuntimeError("index database went away")

    def session_override():
        with session_factory() as session:
            session.commit = broken_commit
            yield session

    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[get_gitea_client] = lambda: fake_gitea
    try:
        client = TestClient(app)
        with pytest.raises(RuntimeError):
            post_archive(client, make_archive())
    finally:
        app.dependency_overrides.clear()

    assert len(fake_gitea.uploads) == 1
    assert len(fake_gitea.deleted) == 1


def test_multi_archive_batch_publishes_both_artifacts(
    client, fake_gitea, session_factory, make_archive
):
    first = make_archive(os_list=("linux", "windows"))
    second = make_archive(os_list=("linux", "windows"), archive_format="zip")
    response = post_archives(client, first, second)
    assert response.status_code == 201, response.text
    body = response.json()
    assert len(body["artifacts"]) == 2
    assert {a["archive_format"] for a in body["artifacts"]} == {"tar.gz", "zip"}
    assert len(fake_gitea.uploads) == 2

    with session_factory() as session:
        versions = session.scalars(select(db.PackageVersion)).all()
        assert len(versions) == 1
        assert len(versions[0].artifacts) == 2


def test_one_bad_archive_rejects_whole_batch(client, fake_gitea, make_archive):
    good = make_archive()
    bad = make_archive(name="other-tool", version="not-semver")
    response = post_archives(client, good, bad)
    assert response.status_code == 422
    assert len(fake_gitea.uploads) == 0
    assert client.get("/packages/kevin-c/my-tool").status_code == 404


def test_content_hash_mismatch_across_batch_is_rejected(client, fake_gitea, make_archive):
    one = make_archive(description="first")
    two = make_archive(description="second", archive_format="zip", os_list=("windows",))
    response = post_archives(client, one, two)
    assert response.status_code == 422
    assert "content" in response.json()["detail"]
    assert len(fake_gitea.uploads) == 0


def test_duplicate_archive_format_in_batch_is_rejected(client, fake_gitea, make_archive):
    archive = make_archive()
    response = post_archives(client, archive, archive)
    assert response.status_code == 422
    assert "tar.gz" in response.json()["detail"]
    assert len(fake_gitea.uploads) == 0


def test_partial_upload_failure_rolls_back_whole_batch(
    client, fake_gitea, make_archive
):
    fake_gitea.fail_upload_after = 1
    first = make_archive(os_list=("linux", "windows"))
    second = make_archive(os_list=("linux", "windows"), archive_format="zip")
    response = post_archives(client, first, second)
    assert response.status_code == 502
    assert len(fake_gitea.uploads) == 1
    assert len(fake_gitea.deleted) == 1
    assert client.get("/packages/kevin-c/my-tool").status_code == 404


def test_failed_index_commit_deletes_every_uploaded_blob_in_batch(
    session_factory, fake_gitea, make_archive
):
    def broken_commit():
        raise RuntimeError("index database went away")

    def session_override():
        with session_factory() as session:
            session.commit = broken_commit
            yield session

    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[get_gitea_client] = lambda: fake_gitea
    try:
        client = TestClient(app)
        first = make_archive(os_list=("linux", "windows"))
        second = make_archive(os_list=("linux", "windows"), archive_format="zip")
        with pytest.raises(RuntimeError):
            post_archives(client, first, second)
    finally:
        app.dependency_overrides.clear()

    assert len(fake_gitea.uploads) == 2
    assert len(fake_gitea.deleted) == 2
