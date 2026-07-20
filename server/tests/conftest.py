import tarfile
import zipfile
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from scripticus_server import db
from scripticus_server.gitea import GiteaAuthError, GiteaError


def make_package_archive(
    tmp_path: Path,
    namespace="kevin-c",
    name="my-tool",
    version="1.0.0",
    os_list=("linux", "macos"),
    language="bash",
    archive_format="tar.gz",
    dependencies=None,
    description="",
) -> Path:
    """Build a valid package tree and archive it the way `pack` does: a
    single root directory named after the package.
    """
    package_dir = tmp_path / f"src-{name}-{version}-{archive_format}" / name
    (package_dir / "src").mkdir(parents=True, exist_ok=True)
    (package_dir / "src" / "main.sh").write_text("echo hello\n")
    dependency_lines = "".join(
        f'"{target}" = "{spec}"\n' for target, spec in (dependencies or {}).items()
    )
    (package_dir / "meta.toml").write_text(
        f"""
[package]
namespace = "{namespace}"
name = "{name}"
version = "{version}"
language = "{language}"
description = "{description}"

[platforms]
os = [{", ".join(f'"{o}"' for o in os_list)}]

[dependencies.packages]
{dependency_lines}
"""
    )

    stem = f"{name.replace('-', '_')}-{version.replace('-', '_')}"
    archive_path = tmp_path / f"{stem}-{'.'.join(os_list)}-{language}.{archive_format}"
    if archive_format == "zip":
        with zipfile.ZipFile(archive_path, "w") as archive:
            for path in sorted(package_dir.rglob("*")):
                archive.write(path, f"{name}/{path.relative_to(package_dir)}")
    else:
        with tarfile.open(archive_path, "w:gz") as archive:
            archive.add(package_dir, arcname=name)
    return archive_path


class FakeGitea:
    """Stands in for GiteaClient behind the publish endpoint's boundary."""

    def __init__(
        self,
        user="kevin-c",
        orgs=(),
        fail_upload=False,
        fail_upload_after=None,
        bad_token=False,
    ):
        self.user = user
        self.orgs = set(orgs)
        self.fail_upload = fail_upload
        self.fail_upload_after = fail_upload_after
        self.bad_token = bad_token
        self.uploads = []
        self.deleted = []

    def authenticated_user(self):
        if self.bad_token:
            raise GiteaAuthError("Gitea rejected the token")
        return self.user

    def can_publish(self, namespace, user):
        return namespace == user or namespace in self.orgs

    def upload_blob(self, namespace, name, version, filename, data):
        if self.fail_upload:
            raise GiteaError("Gitea blob write failed with 500")
        if self.fail_upload_after is not None and len(self.uploads) >= self.fail_upload_after:
            raise GiteaError("Gitea blob write failed with 500")
        self.uploads.append((namespace, name, version, filename, len(data)))
        return f"/api/packages/{namespace}/generic/{name}/{version}/{filename}"

    def delete_blob(self, namespace, name, version, filename):
        self.deleted.append((namespace, name, version, filename))


@pytest.fixture
def make_archive(tmp_path):
    def factory(**kwargs):
        return make_package_archive(tmp_path, **kwargs)

    return factory


@pytest.fixture
def fake_gitea():
    return FakeGitea()


@pytest.fixture
def engine():
    # In-memory SQLite shared across connections, so the app's sessions see
    # what the test seeded.
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    db.init_db(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def session_factory(engine):
    return sessionmaker(bind=engine)
