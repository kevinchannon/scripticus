"""Wire models for the index service's publish (write-path) API (D32).

The request side is deliberately not modelled here: a publish is a
multipart upload of the package archive plus a Gitea token in the
``Authorization`` header, and everything else — identity, variant tags,
dependencies, the content hash — is derived server-side from the archive
itself, which is never trusted to match any client-supplied claims (D8).
Only the response shape is contract.
"""

from pydantic import BaseModel


class PublishedArtifact(BaseModel):
    filename: str
    archive_format: str  # "tar.gz" | "zip"
    platforms: list[str]
    language: str
    size: int


class PublishResult(BaseModel):
    """Response for a successful publish: what the index now records."""

    namespace: str
    name: str
    version: str
    content_hash: str
    publisher: str
    artifact: PublishedArtifact
