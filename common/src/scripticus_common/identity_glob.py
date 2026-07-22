"""Shell-glob matching over a package's ``namespace/name`` identity — the one
rule `list` uses on both sides of the wire (D50).

`list` filters *installed* packages on the client (from the local lockfile)
and *available* packages on the server (from the index), so the two must agree
on exactly what a glob means or the same pattern would yield inconsistent
sections. The primitive therefore lives in the shared contract (D29) rather
than being reimplemented on each side.

Semantics: a pattern containing ``/`` matches the full ``namespace/name`` (so
``acme/*`` scopes by namespace); a bare pattern matches the name alone (so
``db-*`` matches that name in any namespace); no pattern (``None`` or empty)
matches everything. Standard ``fnmatch`` wildcards apply — ``*``, ``?``,
``[seq]``, ``[!seq]`` — deliberately *not* SQL ``LIKE``, whose reduced charset
(only ``%``/``_``) would silently diverge from the client's ``fnmatch``.
"""

import fnmatch


def matches(pattern: str | None, namespace: str, name: str) -> bool:
    """True if ``namespace/name`` satisfies the glob ``pattern`` (D50)."""
    if not pattern:
        return True
    target = f"{namespace}/{name}" if "/" in pattern else name
    return fnmatch.fnmatch(target, pattern)
