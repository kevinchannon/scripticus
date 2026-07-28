"""Command names that would produce an unrunnable shim (D60).

A command name is the last dot-separated segment of every shim (D38), so it is
what an OS reads as the file's extension. `app` is rejected because it would
make those shims look like macOS application bundles.
"""

import pytest

from scripticus_schema.manifest import (
    RESERVED_COMMAND_NAMES,
    ManifestError,
    validate_manifest,
)

BASE = {
    "package": {
        "namespace": "acme",
        "name": "my-tool",
        "version": "1.0.0",
        "language": "bash",
    },
    "platforms": {"os": ["linux", "macos"]},
}


def manifest(**overrides):
    return validate_manifest({**BASE, **overrides})


def validate_or_raise(**overrides):
    try:
        return manifest(**overrides)
    except ManifestError:
        raise


def test_app_is_rejected_as_a_command_name():
    with pytest.raises(ManifestError, match="cannot be a command name"):
        manifest(commands={"app": "src/main.sh"})


def test_app_is_rejected_as_a_package_name_with_no_commands_table():
    # No [commands] means the package name *is* the command name, so the same
    # shim would be produced.
    with pytest.raises(ManifestError, match="cannot be a command name"):
        validate_manifest({**BASE, "package": {**BASE["package"], "name": "app"}})


def test_app_is_rejected_alongside_valid_commands():
    with pytest.raises(ManifestError, match="cannot be a command name"):
        manifest(commands={"backup": "src/backup.sh", "app": "src/app.sh"})


def test_the_error_says_why():
    with pytest.raises(ManifestError) as excinfo:
        manifest(commands={"app": "src/main.sh"})

    message = str(excinfo.value)
    assert ".app" in message
    assert "application bundle" in message


def test_a_package_named_app_may_still_declare_other_commands():
    # Only the command name reaches the end of a shim: `acme.app.backup` is
    # fine, so the package name alone is no problem.
    result = validate_manifest(
        {
            **BASE,
            "package": {**BASE["package"], "name": "app"},
            "commands": {"backup": "src/backup.sh"},
        }
    )

    assert result.package.name == "app"


def test_a_library_named_app_is_fine():
    # A library has no commands and no shims at all, so nothing is named after
    # it on PATH.
    result = validate_manifest(
        {
            **BASE,
            "package": {**BASE["package"], "name": "app"},
            "library": {},
        }
    )

    assert result.library is not None


def test_a_snippet_package_named_app_is_fine():
    # Snippets take no part in the shim system either.
    result = validate_manifest(
        {
            "package": {"namespace": "acme", "name": "app", "version": "1.0.0"},
            "snippet": {"args": {"description": "Argument parsing"}},
        }
    )

    assert result.snippet is not None


@pytest.mark.parametrize("name", ["apps", "app-server", "my-app", "application"])
def test_names_that_merely_contain_app_are_allowed(name):
    # The rule is about the whole final segment, not a substring: `acme.my-app`
    # is not a bundle name.
    assert manifest(commands={name: "src/main.sh"}) is not None


def test_the_reserved_set_is_just_app():
    # Checked by hand: .pkg, .dmg, .command, .workflow and .appx all execute
    # normally, so widening this set would reject names for no reason.
    assert RESERVED_COMMAND_NAMES == frozenset({"app"})
