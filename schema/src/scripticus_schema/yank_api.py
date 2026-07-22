"""Wire models for the index service's yank (write-path) API (D54).

Yank is a mutable, whole-version flag (D16/D23): unlike publish, it changes
nothing in Gitea and stages no blob — it flips one boolean on the version
record. The request carries the desired flag state, so the *same* endpoint
both yanks (``yanked=true``) and, under the client's ``--undo``, un-yanks
(``yanked=false``). Auth is the caller's Gitea token in the ``Authorization``
header, checked live and owner-scoped exactly as publish is (D24/D32); nothing
about identity is trusted from the body — the version is named in the path.
"""

from pydantic import BaseModel


class YankRequest(BaseModel):
    """The desired yank state for a version: ``true`` yanks, ``false`` un-yanks
    (the client's ``--undo``). Reversible either way — there is no window."""

    yanked: bool


class YankResult(BaseModel):
    """What the index records for the version after the change."""

    namespace: str
    name: str
    version: str
    yanked: bool
