"""The filename → snippet-variant rule, computed identically on both sides (D58).

A snippet package holds each snippet's code as flat sibling files under
``src/``: ``src/<name>.<ext>``, one per language the author wrote it in. The
author never enumerates those languages — they are *derived* from the tree, by
the server when it projects an uploaded archive into the index and by the
client when it reads its own installed package. Both must derive the same set
from the same tree, so the rule lives here rather than twice (D51).

The label **is** the raw extension (``py``, ``cpp``, ``sh``) — the same token
the user types at ``snip args.py``. A snippet is never executed, so it needs no
interpreter and no ``LANGUAGES`` entry, which is what lets snippets carry
boilerplate for languages Scripticus can never run as commands (D58).
"""

import posixpath

SNIPPET_SOURCE_DIR = "src"


def variant_of(path: str) -> tuple[str, str] | None:
    """The ``(name, extension)`` a snippet source path denotes, or None.

    ``path`` is package-relative and POSIX-separated (the form archive members
    and lockfile entries take). Only flat ``src/<name>.<ext>`` files qualify:
    anything nested deeper, outside ``src/``, or without an extension is not a
    snippet variant — a snippet package may still carry a README, a LICENSE, or
    its own subdirectories without them being mistaken for code.
    """
    directory, _, filename = path.replace("\\", "/").rpartition("/")
    if directory != SNIPPET_SOURCE_DIR:
        return None
    name, dot, extension = filename.rpartition(".")
    if not dot or not name or not extension:
        return None
    return name, extension


def variants_from_paths(paths) -> dict[str, list[str]]:
    """Map every snippet name in ``paths`` to its sorted extensions.

    ``paths`` is any iterable of package-relative POSIX paths. Names absent
    from the manifest are included; reconciling the derived set against the
    declared ``[snippet.<name>]`` sections is the caller's job (the server
    rejects a mismatch at publish, D58's manifest-is-intent boundary).
    """
    variants: dict[str, set[str]] = {}
    for path in paths:
        variant = variant_of(path)
        if variant is not None:
            name, extension = variant
            variants.setdefault(name, set()).add(extension)
    return {name: sorted(extensions) for name, extensions in sorted(variants.items())}


def variant_path(name: str, extension: str) -> str:
    """The package-relative path a snippet variant lives at."""
    return posixpath.join(SNIPPET_SOURCE_DIR, f"{name}.{extension}")
