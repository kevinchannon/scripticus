"""Reading installed snippets (`snip`, D58).

A snippet is boilerplate you paste and edit — never run, never sourced — so
this is the one read path that touches no shim, no PATH, and no staging. It
resolves a reference against the lockfile, then reads a file out of the
installed tree. Nothing here writes anything (the `--copy` tee lives in the
CLI, where the side effect is visible).

Reference grammar, terse by design because `snip` competes with retyping the
boilerplate, not with other package managers:

    args.sh            a snippet name and its language, the everyday form
    args               the name alone; unambiguous only if one variant exists
    acme/boiler:args   fully namespaced, the escape hatch (D46)
    acme/boiler:args.sh

Ambiguity always **lists, never guesses** (D58): whether two packages both
provide ``args.sh`` or one snippet exists in three languages, the candidates
come back in the error for the user to pick from. A read is cheap and
side-effect-free, so there is nothing to be gained by resolving to a winner —
and consequently nothing anywhere records who "owns" a snippet name.
"""

from dataclasses import dataclass
from pathlib import Path

from scripticus_common.snippet_variants import variant_path


class SnippetError(Exception):
    """A snippet reference could not be resolved to exactly one snippet."""


@dataclass(frozen=True)
class Reference:
    """A parsed `snip` argument."""

    package: str | None  # "namespace/name", or None for any installed package
    name: str
    extension: str | None  # None when the user typed no extension


@dataclass(frozen=True)
class Candidate:
    """One installed snippet variant a reference could mean."""

    namespace: str
    name: str  # the package name
    snippet: str
    extension: str
    path: Path  # the file to read

    @property
    def package_id(self) -> str:
        return f"{self.namespace}/{self.name}"

    @property
    def reference(self) -> str:
        """The fully qualified form that names this candidate and no other."""
        return f"{self.package_id}:{self.snippet}.{self.extension}"


def parse_reference(token: str) -> Reference:
    """Parse a `snip` argument; raise SnippetError if it is malformed."""
    package, separator, remainder = token.rpartition(":")
    if separator and not package:
        raise SnippetError(f"'{token}' has an empty package qualifier")
    if package and "/" not in package:
        raise SnippetError(
            f"'{token}' is not a fully namespaced reference"
            " — write it as 'namespace/package:snippet'"
        )
    name, dot, extension = remainder.rpartition(".")
    if not dot:
        name, extension = remainder, None
    if not name or extension == "":
        raise SnippetError(f"'{token}' is not a snippet reference (name[.ext])")
    return Reference(package=package or None, name=name, extension=extension)


def candidates(lock: dict, home: Path, reference: Reference) -> list[Candidate]:
    """Every installed snippet variant matching ``reference``, in a stable
    order (package identity, then extension).

    Read from the lockfile's per-package snippet map, which install derives
    from the tree by the shared rule (D51), so the client agrees with the
    index's projection of the same package.
    """
    found = []
    for entry in lock["packages"]:
        package_id = f"{entry['namespace']}/{entry['name']}"
        if reference.package is not None and reference.package != package_id:
            continue
        extensions = entry.get("snippets", {}).get(reference.name, [])
        for extension in extensions:
            if reference.extension is not None and reference.extension != extension:
                continue
            found.append(
                Candidate(
                    namespace=entry["namespace"],
                    name=entry["name"],
                    snippet=reference.name,
                    extension=extension,
                    path=home
                    / "pkgs"
                    / entry["namespace"]
                    / entry["name"]
                    / entry["version"]
                    / variant_path(reference.name, extension),
                )
            )
    return sorted(found, key=lambda c: (c.namespace, c.name, c.extension))


def _near_misses(lock: dict, reference: Reference) -> list[str]:
    """Extensions the named snippet *does* exist in, for a wrong-extension
    reference — the difference between "no such snippet" and "not in that
    language" is worth one line of help."""
    extensions = set()
    for entry in lock["packages"]:
        extensions.update(entry.get("snippets", {}).get(reference.name, []))
    return sorted(extensions)


def resolve(lock: dict, home: Path, token: str) -> Candidate:
    """Resolve a reference to exactly one installed snippet, or explain why
    it cannot be: nothing matches, or several do (listed, never guessed).
    """
    reference = parse_reference(token)
    matches = candidates(lock, home, reference)

    if not matches:
        available = _near_misses(lock, reference)
        if available and reference.extension is not None:
            listed = ", ".join(f"{reference.name}.{ext}" for ext in available)
            raise SnippetError(
                f"no snippet '{token}' is installed — '{reference.name}' is"
                f" available as: {listed}"
            )
        raise SnippetError(
            f"no snippet '{token}' is installed"
            " — 'scripticus search' finds published ones"
        )

    if len(matches) > 1:
        listed = "\n".join(f"  {candidate.reference}" for candidate in matches)
        raise SnippetError(f"'{token}' is ambiguous; it could be:\n{listed}")

    candidate = matches[0]
    if not candidate.path.is_file():
        raise SnippetError(
            f"'{candidate.reference}' is recorded as installed but"
            f" '{candidate.path}' is missing — reinstall {candidate.package_id}"
        )
    return candidate


def read(candidate: Candidate) -> str:
    """The snippet's text, verbatim.

    Read as text and written back unchanged: a snippet is source the user is
    about to paste, so it must not be reformatted, highlighted, or re-wrapped
    on the way out.
    """
    try:
        return candidate.path.read_text()
    except OSError as exc:
        raise SnippetError(f"cannot read '{candidate.path}': {exc}") from exc
