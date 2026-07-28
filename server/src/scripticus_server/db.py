"""The index data model (ARCHITECTURE.md), SQLite via SQLAlchemy (D23).

No SQLite-isms: everything here must work unchanged on Postgres, so the
database stays a configuration change (``SCRIPTICUS_INDEX_DB``). Tables are
created with ``create_all`` at startup — no migration tool until the schema
has released consumers (D31).

The extracted columns are a publish-time projection of the verbatim
manifest blob, never independently editable (D21).
"""

import os
from collections.abc import Iterator

from sqlalchemy import ForeignKey, Text, UniqueConstraint, create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    relationship,
    sessionmaker,
)

DEFAULT_DB_URL = "sqlite:///scripticus-index.db"


def database_url() -> str:
    return os.environ.get("SCRIPTICUS_INDEX_DB", DEFAULT_DB_URL)


def make_engine(url: str | None = None) -> Engine:
    return create_engine(url or database_url())


def init_db(engine: Engine) -> None:
    Base.metadata.create_all(engine)


_session_factory: sessionmaker | None = None


def get_session() -> Iterator[Session]:
    # FastAPI dependency. Lazy so that importing the app never touches the
    # database; tables are created on first use (D31: create_all until the
    # schema stabilises).
    global _session_factory
    if _session_factory is None:
        engine = make_engine()
        init_db(engine)
        _session_factory = sessionmaker(bind=engine)
    with _session_factory() as session:
        yield session


class Base(DeclarativeBase):
    pass


class Namespace(Base):
    """A cached reference to a Gitea user/org — an FK anchor only. Gitea
    remains authoritative for ownership and ACLs; nothing permission-shaped
    is ever stored here (D24).
    """

    __tablename__ = "namespace"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(unique=True)

    packages: Mapped[list["Package"]] = relationship(back_populates="namespace")


class Package(Base):
    __tablename__ = "package"
    __table_args__ = (UniqueConstraint("namespace_id", "name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    namespace_id: Mapped[int] = mapped_column(ForeignKey("namespace.id"))
    name: Mapped[str]

    namespace: Mapped[Namespace] = relationship(back_populates="packages")
    versions: Mapped[list["PackageVersion"]] = relationship(back_populates="package")


class PackageVersion(Base):
    """Immutable once written; yank is a whole-version flag (npm model)."""

    __tablename__ = "package_version"
    __table_args__ = (UniqueConstraint("package_id", "version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    package_id: Mapped[int] = mapped_column(ForeignKey("package.id"))
    version: Mapped[str]
    description: Mapped[str] = mapped_column(default="")
    yanked: Mapped[bool] = mapped_column(default=False)
    published_at: Mapped[str] = mapped_column(default="")  # ISO 8601 UTC
    publisher: Mapped[str] = mapped_column(default="")

    package: Mapped[Package] = relationship(back_populates="versions")
    artifacts: Mapped[list["Artifact"]] = relationship(back_populates="package_version")
    dependencies: Mapped[list["Dependency"]] = relationship()
    tool_deps: Mapped[list["ToolDep"]] = relationship()
    commands: Mapped[list["Command"]] = relationship()
    snippets: Mapped[list["Snippet"]] = relationship()
    libraries: Mapped[list["Library"]] = relationship()
    manifest_blob: Mapped["ManifestBlob | None"] = relationship(
        back_populates="package_version"
    )


class Artifact(Base):
    """One per platform/language variant of a version."""

    __tablename__ = "artifact"

    id: Mapped[int] = mapped_column(primary_key=True)
    package_version_id: Mapped[int] = mapped_column(ForeignKey("package_version.id"))
    # Comma-joined OS names from the manifest's platforms.os — a queryable
    # projection; the manifest blob remains the authoritative list.
    platforms: Mapped[str]
    language: Mapped[str]
    archive_format: Mapped[str] = mapped_column(default="")
    content_hash: Mapped[str] = mapped_column(default="")
    size: Mapped[int] = mapped_column(default=0)
    gitea_pointer: Mapped[str] = mapped_column(default="")

    package_version: Mapped[PackageVersion] = relationship(back_populates="artifacts")

    def platform_list(self) -> list[str]:
        return self.platforms.split(",") if self.platforms else []


class Dependency(Base):
    __tablename__ = "dependency"

    id: Mapped[int] = mapped_column(primary_key=True)
    package_version_id: Mapped[int] = mapped_column(ForeignKey("package_version.id"))
    target: Mapped[str]  # fully namespaced: "owner/name"
    spec: Mapped[str]  # semver range constraint


class ToolDep(Base):
    __tablename__ = "tool_dep"

    id: Mapped[int] = mapped_column(primary_key=True)
    package_version_id: Mapped[int] = mapped_column(ForeignKey("package_version.id"))
    name: Mapped[str]
    required: Mapped[bool] = mapped_column(default=True)


class Command(Base):
    __tablename__ = "command"

    id: Mapped[int] = mapped_column(primary_key=True)
    package_version_id: Mapped[int] = mapped_column(ForeignKey("package_version.id"))
    name: Mapped[str]
    script_path: Mapped[str]


class Snippet(Base):
    """One snippet a version provides (D58).

    ``name`` and ``description`` are the manifest's ``[snippet.<name>]``
    section; ``extensions`` is *derived* from the package tree at publish — the
    author never lists the languages, so this column is a projection of content
    rather than of the manifest, but it is re-derivable from the archive all
    the same (D21). Comma-joined, following Artifact.platforms.
    """

    __tablename__ = "snippet"

    id: Mapped[int] = mapped_column(primary_key=True)
    package_version_id: Mapped[int] = mapped_column(ForeignKey("package_version.id"))
    name: Mapped[str]
    description: Mapped[str] = mapped_column(default="")
    extensions: Mapped[str] = mapped_column(default="")

    def extension_list(self) -> list[str]:
        return self.extensions.split(",") if self.extensions else []


class Library(Base):
    """Present iff a version is a library package (D57) — at most one row.

    A separate table rather than a ``kind`` column on ``package_version``:
    ``create_all`` (D31) creates new tables but never alters existing ones, and
    the server has released consumers, so a new column would silently not appear
    on an existing index. ``entrypoint`` is the ``src/load.<ext>`` path the
    consumer sources — derivable from the manifest's language, stored so a
    reader needs no language table.
    """

    __tablename__ = "library"

    id: Mapped[int] = mapped_column(primary_key=True)
    package_version_id: Mapped[int] = mapped_column(
        ForeignKey("package_version.id"), unique=True
    )
    entrypoint: Mapped[str] = mapped_column(default="")


class ManifestBlob(Base):
    """The verbatim manifest as published — the authoritative record every
    extracted column above is re-derivable from (D21).
    """

    __tablename__ = "manifest_blob"

    id: Mapped[int] = mapped_column(primary_key=True)
    package_version_id: Mapped[int] = mapped_column(
        ForeignKey("package_version.id"), unique=True
    )
    toml: Mapped[str] = mapped_column(Text)

    package_version: Mapped[PackageVersion] = relationship(
        back_populates="manifest_blob"
    )
