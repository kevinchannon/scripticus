"""The library package kind (D57): a fieldless [library] marker, a shell-only
language, and a src/load.<ext> entry point instead of commands."""

from pathlib import Path

import pytest

from scripticus_schema.manifest import (
    LANGUAGES,
    ManifestError,
    commands_of,
    is_library,
    is_snippet,
    kind_of,
    language_tag,
    library_entrypoint,
    load_manifest,
    target_platforms,
    validate_manifest,
)

LIBRARY_MANIFEST = """\
[package]
namespace = "acme"
name = "strings"
version = "1.0.0"
language = "bash"
description = "String helpers we source everywhere"

[platforms]
os = ["linux", "macos"]

[library]
"""


def write_package(
    tmp_path: Path, manifest_text=LIBRARY_MANIFEST, files=("src/load.sh",)
) -> Path:
    package_dir = tmp_path / "strings"
    package_dir.mkdir()
    (package_dir / "meta.toml").write_text(manifest_text)
    for name in files:
        path = package_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("scr_strings_upper() { printf '%s' \"$1\" | tr a-z A-Z; }\n")
    return package_dir


def test_library_package_is_marked_by_a_fieldless_table(tmp_path):
    manifest = load_manifest(write_package(tmp_path))

    assert is_library(manifest)
    assert not is_snippet(manifest)
    assert kind_of(manifest) == "library"
    # The marker's presence is the whole declaration — it enumerates nothing.
    assert manifest.library.model_dump() == {}


def test_library_declares_language_and_platforms_like_a_command(tmp_path):
    # Unlike a snippet, a library really is shell code for real platforms, so
    # both stay required and the archive tag carries the real language.
    manifest = load_manifest(write_package(tmp_path))

    assert language_tag(manifest) == "bash"
    assert target_platforms(manifest) == ["linux", "macos"]


def test_library_provides_no_commands(tmp_path):
    # A library is sourced by name through scr_load, never invoked through
    # PATH, so it takes no part in the shim system at all.
    manifest = load_manifest(write_package(tmp_path))

    assert commands_of(manifest) == {}


def test_entrypoint_follows_the_declared_language(tmp_path):
    manifest = load_manifest(write_package(tmp_path))
    assert library_entrypoint(manifest) == "src/load.sh"


def test_missing_entrypoint_is_an_error(tmp_path):
    with pytest.raises(ManifestError, match=r"no src/load.sh to source"):
        load_manifest(write_package(tmp_path, files=("src/helpers.sh",)))


def test_library_cannot_also_declare_commands(tmp_path):
    text = LIBRARY_MANIFEST + '\n[commands]\nstrings = "src/main.sh"\n'
    with pytest.raises(ManifestError, match="cannot also declare"):
        load_manifest(write_package(tmp_path, manifest_text=text))


def test_library_cannot_also_declare_snippets(tmp_path):
    text = LIBRARY_MANIFEST + '\n[snippet.args]\ndescription = "Args"\n'
    with pytest.raises(ManifestError, match="cannot also declare"):
        load_manifest(write_package(tmp_path, manifest_text=text))


@pytest.mark.parametrize("language", ["python", "powershell"])
def test_a_library_must_be_shell(language):
    # The scope decision (D57): languages with their own package manager are a
    # deliberate non-goal, so this is rejected at the manifest rather than
    # silently distributing something nothing can source.
    text = LIBRARY_MANIFEST.replace('language = "bash"', f'language = "{language}"')
    with pytest.raises(ManifestError, match="cannot be written in"):
        validate_manifest_text(text)


def test_sh_is_a_language_in_its_own_right():
    # Added for libraries, but it makes portable `sh` commands possible too.
    # It shares bash's extension: dialect comes from the declared language.
    assert LANGUAGES["sh"].extension == "sh"
    assert LANGUAGES["sh"].interpreter == "sh"


def test_an_sh_library_is_valid(tmp_path):
    text = LIBRARY_MANIFEST.replace('language = "bash"', 'language = "sh"')
    manifest = load_manifest(write_package(tmp_path, manifest_text=text))

    assert is_library(manifest)
    assert library_entrypoint(manifest) == "src/load.sh"


def validate_manifest_text(text: str):
    """Validate manifest text alone (no tree), raising ManifestError like
    load_manifest does — for the checks that never reach the filesystem."""
    import tomllib

    return validate_manifest(tomllib.loads(text))
