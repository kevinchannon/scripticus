import pytest
from sqlalchemy.exc import IntegrityError

from scripticus_server import db


def test_init_db_is_idempotent(engine):
    db.init_db(engine)  # second call must not fail


def test_namespace_names_are_unique(session_factory):
    with session_factory() as session:
        session.add(db.Namespace(name="kevin-c"))
        session.add(db.Namespace(name="kevin-c"))
        with pytest.raises(IntegrityError):
            session.commit()


def test_package_names_are_unique_within_a_namespace(session_factory):
    with session_factory() as session:
        namespace = db.Namespace(name="kevin-c")
        session.add(db.Package(namespace=namespace, name="my-tool"))
        session.add(db.Package(namespace=namespace, name="my-tool"))
        with pytest.raises(IntegrityError):
            session.commit()


def test_same_package_name_may_exist_in_different_namespaces(session_factory):
    with session_factory() as session:
        session.add(db.Package(namespace=db.Namespace(name="kevin-c"), name="my-tool"))
        session.add(db.Package(namespace=db.Namespace(name="other"), name="my-tool"))
        session.commit()


def test_versions_are_unique_within_a_package(session_factory):
    with session_factory() as session:
        package = db.Package(namespace=db.Namespace(name="kevin-c"), name="my-tool")
        session.add(db.PackageVersion(package=package, version="1.0.0"))
        session.add(db.PackageVersion(package=package, version="1.0.0"))
        with pytest.raises(IntegrityError):
            session.commit()


def test_a_version_has_at_most_one_manifest_blob(session_factory):
    with session_factory() as session:
        package = db.Package(namespace=db.Namespace(name="kevin-c"), name="my-tool")
        version = db.PackageVersion(package=package, version="1.0.0")
        session.add(db.ManifestBlob(package_version=version, toml="[package]"))
        session.add(db.ManifestBlob(package_version=version, toml="[package]"))
        with pytest.raises(IntegrityError):
            session.commit()


def test_artifact_platform_list_round_trips(session_factory):
    with session_factory() as session:
        package = db.Package(namespace=db.Namespace(name="kevin-c"), name="my-tool")
        version = db.PackageVersion(package=package, version="1.0.0")
        artifact = db.Artifact(
            package_version=version, platforms="linux,macos", language="bash"
        )
        session.add(artifact)
        session.commit()
        assert artifact.platform_list() == ["linux", "macos"]
