"""Content-addressed package identity: a Merkle hash of a directory tree (D3).

The canonical identity of a package artifact is the hash of its directory
tree, git-style: files hash their contents, directories hash their sorted
entry listing. File modes are deliberately excluded — zip extraction drops
the executable bit, and the same content must hash identically whichever
archive container it travelled in (D26).
"""

import hashlib
from pathlib import Path


def _blob_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _tree_digest(directory: Path) -> str:
    lines = []
    for child in sorted(directory.iterdir(), key=lambda p: p.name):
        if child.is_dir():
            lines.append(f"tree {_tree_digest(child)} {child.name}\n")
        else:
            lines.append(f"blob {_blob_hash(child)} {child.name}\n")
    return hashlib.sha256("".join(lines).encode()).hexdigest()


def tree_hash(directory: Path) -> str:
    """The content hash of a package directory, as ``sha256:<hex>``."""
    return f"sha256:{_tree_digest(directory)}"
