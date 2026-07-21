import pytest

from scripticus_schema.version_spec import (
    VersionSpec,
    VersionSpecError,
    parse,
    select_version,
)


def matches(spec: str, version: str) -> bool:
    return parse(spec).matches(version)


# --- Exact ------------------------------------------------------------------


def test_bare_full_version_is_exact():
    assert matches("1.2.3", "1.2.3")
    assert not matches("1.2.3", "1.2.4")
    assert not matches("1.2.3", "1.2.2")


def test_equals_operator_is_exact():
    assert matches("=1.2.3", "1.2.3")
    assert not matches("=1.2.3", "1.3.0")


def test_bare_partial_version_is_rejected_as_ambiguous():
    with pytest.raises(VersionSpecError, match="full version"):
        parse("1.2")


# --- Caret ------------------------------------------------------------------


@pytest.mark.parametrize(
    "spec, low_ok, high_bad",
    [
        ("^1.2.3", "1.2.3", "2.0.0"),
        ("^1.2", "1.2.0", "2.0.0"),
        ("^1", "1.0.0", "2.0.0"),
        ("^0.2.3", "0.2.3", "0.3.0"),
        ("^0.2", "0.2.0", "0.3.0"),
        ("^0.0.3", "0.0.3", "0.0.4"),
        ("^0.0", "0.0.0", "0.1.0"),
        ("^0", "0.0.0", "1.0.0"),
    ],
)
def test_caret_bounds(spec, low_ok, high_bad):
    assert matches(spec, low_ok)
    assert not matches(spec, high_bad)


def test_caret_matches_between_bounds():
    assert matches("^1.2.3", "1.9.9")
    assert not matches("^1.2.3", "1.2.2")


# --- Comparators & intersection --------------------------------------------


def test_comparators_pad_partial_operands():
    assert matches(">=1.2", "1.2.0")
    assert not matches(">=1.2", "1.1.9")
    assert matches("<2", "1.9.9")
    assert not matches("<2", "2.0.0")


def test_comma_is_intersection():
    assert matches(">=1.2.0, <2.0.0", "1.5.0")
    assert not matches(">=1.2.0, <2.0.0", "2.0.0")
    assert not matches(">=1.2.0, <2.0.0", "1.1.0")


def test_star_and_empty_match_any_release():
    assert matches("*", "9.9.9")
    assert matches("", "0.0.1")


# --- Prereleases ------------------------------------------------------------


def test_ranges_never_match_prereleases():
    assert not matches("^1.0.0", "1.5.0-rc1")
    assert not matches(">=1.0.0", "2.0.0-alpha")
    assert not matches("*", "1.0.0-rc1")


def test_prerelease_is_opt_in_via_exact_pin():
    assert matches("1.0.0-rc1", "1.0.0-rc1")
    assert matches("=1.0.0-rc1", "1.0.0-rc1")
    assert not matches("1.0.0", "1.0.0-rc1")  # release pin excludes the prerelease


# --- select_version (the window primitive) ----------------------------------


def test_select_returns_highest_in_window():
    candidates = ["1.0.0", "1.2.0", "1.5.3", "2.0.0"]
    assert select_version(["^1.2"], candidates) == "1.5.3"


def test_select_intersects_multiple_specs():
    candidates = ["1.0.0", "1.2.0", "1.4.0", "1.9.0"]
    assert select_version([">=1.2.0", "<1.9.0"], candidates) == "1.4.0"


def test_select_returns_none_on_empty_window():
    assert select_version(["^3.0"], ["1.0.0", "2.0.0"]) is None


def test_select_accepts_already_parsed_specs():
    already_parsed: VersionSpec = parse("^1")
    assert select_version([already_parsed], ["1.4.0"]) == "1.4.0"


def test_select_no_specs_picks_highest_release():
    assert select_version([], ["1.0.0", "1.3.0", "1.3.0-rc1"]) == "1.3.0"


# --- Errors -----------------------------------------------------------------


def test_garbage_spec_raises():
    with pytest.raises(VersionSpecError):
        parse("not-a-version")


def test_operator_without_operand_raises():
    with pytest.raises(VersionSpecError, match="no version after"):
        parse(">=")


def test_matches_rejects_non_semver_candidate():
    with pytest.raises(VersionSpecError, match="valid semver"):
        parse("^1.0").matches("1.0")
