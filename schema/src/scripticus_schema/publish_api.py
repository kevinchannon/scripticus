"""Wire models for the index service's publish (write-path) API (D32, D37).

The request side is deliberately not modelled here: a publish is a
multipart upload of one or more package archives (validated and committed
atomically as a batch, D37) plus a Gitea token in the ``Authorization``
header, and everything else — identity, variant tags, dependencies, the
content hash — is derived server-side from the archives themselves, which
are never trusted to match any client-supplied claims (D8). Only the
response shape is contract.
"""

from pydantic import BaseModel


class PublishedArtifact(BaseModel):
    filename: str
    archive_format: str  # "tar.gz" | "zip"
    platforms: list[str]
    language: str
    size: int


class PublishResult(BaseModel):
    """Response for a successful publish: what the index now records.

    ``artifacts`` is always the full batch (D37): a publish either commits
    every archive the client sent, or none of them.
    """

    namespace: str
    name: str
    version: str
    content_hash: str
    publisher: str
    artifacts: list[PublishedArtifact]
