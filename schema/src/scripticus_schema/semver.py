"""Strict semver: the official pattern and precedence ordering (D16)."""

import re

# The official semver.org regex.
SEMVER_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)"
    r"(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?"
    r"(?:\+([0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?$"
)


def semver_key(version: str):
    """A sort key implementing semver precedence (build metadata ignored).

    Assumes ``version`` already matches ``SEMVER_RE``.
    """
    core = version.partition("+")[0]
    numbers, _, prerelease = core.partition("-")
    major, minor, patch = (int(part) for part in numbers.split("."))
    if not prerelease:
        # A release sorts after every prerelease of the same version.
        return (major, minor, patch, 1, ())
    identifiers = tuple(
        (0, int(ident), "") if ident.isdigit() else (1, 0, ident)
        for ident in prerelease.split(".")
    )
    return (major, minor, patch, 0, identifiers)
