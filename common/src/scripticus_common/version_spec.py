"""Version specifications: caret and comparator ranges over strict semver.

A dependency spec — the string value in a manifest's
``[dependencies.packages]`` (e.g. ``"^2.0"`` or ``"1.4.3"``) and the
``@<spec>`` in ``install pkg@<spec>`` — is parsed here into a predicate
over published versions. ``select_version`` picks the highest candidate
satisfying a set of specs: the reusable *version-window primitive* that
resolution runs for both packages (against the index) and tools (D42/D43).

Grammar (npm/cargo-style caret, as the manifest and CLI already use):

  ``^1.2.3``  caret: ``>=1.2.3`` and ``<2.0.0``. The upper bound comes from
              the left-most non-zero component, so ``^0.2.3`` is ``<0.3.0``
              and ``^0.0.3`` is ``<0.0.4``. The operand may be partial:
              ``^1``, ``^1.2``.
  ``>=1.2``   comparators (``>=`` ``>`` ``<=`` ``<`` ``=``); a comma joins
              them as AND (intersection), e.g. ``">=1.2, <2.0.0"``. Partial
              operands pad with zeros (``>=1.2`` is ``>=1.2.0``).
  ``1.4.3``   a bare full version is an exact match (equivalent to
              ``=1.4.3``); a bare *partial* is rejected as ambiguous.
  ``*``       (or the empty string) any released version.

Prereleases are opt-in: a prerelease version (``1.0.0-rc1``) is selected
only by an exact pin equal to it; caret and comparator ranges never match
a prerelease candidate, so a plain ``install foo`` never surprises anyone
with a release candidate.
"""

from collections.abc import Callable, Iterable

from scripticus_common.semver import SEMVER_RE, semver_key

_COMPARATORS = ("<=", ">=", "<", ">", "=")

# A parsed clause: given a candidate's (version, sort key, is-prerelease),
# does it satisfy this clause?
_Clause = Callable[[str, tuple, bool], bool]


class VersionSpecError(ValueError):
    """A version spec could not be parsed."""


def _release_key(major: int, minor: int, patch: int) -> tuple:
    """The ``semver_key`` of a release ``major.minor.patch`` (no prerelease)."""
    return (major, minor, patch, 1, ())


def _has_prerelease(version: str) -> bool:
    return "-" in version.partition("+")[0]


def _parse_partial(text: str) -> tuple[int, int, int, int]:
    """A 1–3 component numeric version; returns padded (major, minor, patch)
    plus how many components were actually given. No prerelease/build.
    """
    parts = text.split(".")
    if not (1 <= len(parts) <= 3):
        raise VersionSpecError(f"'{text}' is not a version (expected up to three parts)")
    numbers = []
    for part in parts:
        if not part.isdigit() or (len(part) > 1 and part[0] == "0"):
            raise VersionSpecError(f"'{text}' has a non-numeric or zero-padded component")
        numbers.append(int(part))
    padded = (numbers + [0, 0])[:3]
    return padded[0], padded[1], padded[2], len(parts)


def _caret_bounds(major: int, minor: int, patch: int, given: int) -> tuple[tuple, tuple]:
    """Cargo's caret rule: the upper bound increments the left-most non-zero
    component, accounting for how many components were specified.
    """
    if major != 0:
        upper = (major + 1, 0, 0)
    elif given >= 2 and minor != 0:
        upper = (0, minor + 1, 0)
    elif given >= 3:  # major 0, minor 0, patch specified: ^0.0.p -> <0.0.(p+1)
        upper = (0, 0, patch + 1)
    elif given == 2:  # ^0.0 -> <0.1.0
        upper = (0, 1, 0)
    else:  # ^0 -> <1.0.0
        upper = (1, 0, 0)
    return (major, minor, patch), upper


def _caret_clause(operand: str) -> _Clause:
    major, minor, patch, given = _parse_partial(operand)
    lower, upper = _caret_bounds(major, minor, patch, given)
    lo, hi = _release_key(*lower), _release_key(*upper)

    def clause(version: str, key: tuple, is_pre: bool) -> bool:
        return not is_pre and lo <= key < hi

    return clause


def _comparator_clause(op: str, operand: str) -> _Clause:
    if op == "=":
        return _exact_clause(operand)
    major, minor, patch, _ = _parse_partial(operand)
    bound = _release_key(major, minor, patch)
    op_test = {
        ">=": lambda key: key >= bound,
        ">": lambda key: key > bound,
        "<=": lambda key: key <= bound,
        "<": lambda key: key < bound,
    }[op]

    def clause(version: str, key: tuple, is_pre: bool) -> bool:
        return not is_pre and op_test(key)

    return clause


def _exact_clause(operand: str) -> _Clause:
    if not SEMVER_RE.match(operand):
        raise VersionSpecError(
            f"'{operand}' is not a full version; an exact match needs"
            " major.minor.patch (use ^ for a range)"
        )
    target = semver_key(operand)

    def clause(version: str, key: tuple, is_pre: bool) -> bool:
        return key == target

    return clause


def _any_clause(version: str, key: tuple, is_pre: bool) -> bool:
    return not is_pre


def _parse_clause(text: str) -> _Clause:
    if text.startswith("^"):
        return _caret_clause(text[1:])
    for op in _COMPARATORS:
        if text.startswith(op):
            operand = text[len(op) :].strip()
            if not operand:
                raise VersionSpecError(f"'{text}' has no version after '{op}'")
            return _comparator_clause(op, operand)
    # No operator: a bare version is an exact match (must be full semver).
    return _exact_clause(text)


class VersionSpec:
    """A parsed dependency spec; ``matches`` tests a candidate version."""

    def __init__(self, clauses: list[_Clause]):
        self._clauses = clauses

    def matches(self, version: str) -> bool:
        if not SEMVER_RE.match(version):
            raise VersionSpecError(f"'{version}' is not a valid semver version")
        key = semver_key(version)
        is_pre = _has_prerelease(version)
        return all(clause(version, key, is_pre) for clause in self._clauses)


def parse(spec: str) -> VersionSpec:
    """Parse a spec string; raises ``VersionSpecError`` on bad syntax."""
    text = spec.strip()
    if text in ("", "*"):
        return VersionSpec([_any_clause])
    clauses = []
    for raw in text.split(","):
        clause_text = raw.strip()
        if not clause_text:
            raise VersionSpecError(f"'{spec}' has an empty clause")
        clauses.append(_parse_clause(clause_text))
    return VersionSpec(clauses)


def select_version(
    specs: Iterable[str | VersionSpec], candidates: Iterable[str]
) -> str | None:
    """The highest candidate satisfying every spec (their intersection), or
    ``None`` if the window is empty. Candidates need not be pre-sorted.

    This is the window primitive: for packages ``candidates`` are the index's
    published versions; for tools they are whatever the local package manager
    offers (D43).
    """
    parsed = [s if isinstance(s, VersionSpec) else parse(s) for s in specs]
    eligible = [c for c in candidates if all(spec.matches(c) for spec in parsed)]
    if not eligible:
        return None
    return max(eligible, key=semver_key)
