"""Content-addressed package identity: a Merkle hash of a directory tree (D3).

The canonical identity of a package artifact is the hash of its directory
tree, git-style: files hash their contents, directories hash their sorted
entry listing. Entry names are length-prefixed in the listing so the
encoding is injective — a crafted file name cannot forge record structure,
so distinct trees cannot hash identically (D27). File modes are deliberately
excluded — zip extraction drops the executable bit, and the same content
must hash identically whichever archive container it travelled in (D26).

The client verifies this hash after download; the index verifies it at
publish. Both sides use this module, so the implementations cannot diverge
(D29).
"""

import hashlib
from pathlib import Path


def _blob_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _tree_digest(directory: Path) -> str:
    records = []
    for child in sorted(directory.iterdir(), key=lambda p: p.name):
        kind = "tree" if child.is_dir() else "blob"
        digest = _tree_digest(child) if child.is_dir() else _blob_hash(child)
        name = child.name.encode()
        records.append(b"%s %s %d:%s\n" % (kind.encode(), digest.encode(), len(name), name))
    return hashlib.sha256(b"".join(records)).hexdigest()


def tree_hash(directory: Path) -> str:
    """The content hash of a package directory, as ``sha256:<hex>``."""
    return f"sha256:{_tree_digest(directory)}"
