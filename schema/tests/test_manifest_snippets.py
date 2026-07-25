"""The snippet package kind (D58): a manifest with [snippet.<name>] sections,
no package language, and no required platform."""

from pathlib import Path

import pytest

from scripticus_schema.manifest import (
    ManifestError,
    commands_of,
    extension_for_language,
    is_snippet,
    language_tag,
    load_manifest,
    snippet_variants,
    target_platforms,
    validate_manifest,
)

SNIPPET_MANIFEST = """\
[package]
namespace = "acme"
name = "boilerplate"
version = "1.0.0"
description = "Boilerplate we always retype"

[snippet.args]
description = "Argument parsing"
"""


def write_package(tmp_path: Path, manifest_text=SNIPPET_MANIFEST, files=("src/args.sh",)) -> Path:
    package_dir = tmp_path / "boilerplate"
    package_dir.mkdir()
    (package_dir / "meta.toml").write_text(manifest_text)
    for name in files:
        path = package_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# boilerplate\n")
    return package_dir


def test_snippet_package_needs_no_language_or_platforms(tmp_path):
    manifest = load_manifest(write_package(tmp_path))

    assert is_snippet(manifest)
    assert manifest.package.language is None
    assert manifest.platforms is None
    assert manifest.snippet["args"].description == "Argument parsing"


def test_language_and_platform_fall_back_to_the_any_sentinels(tmp_path):
    manifest = load_manifest(write_package(tmp_path))

    # Downstream machinery (archive tag, index row, install's platform check)
    # never sees a None: it sees a sentinel and the expanded OS list.
    assert language_tag(manifest) == "snippet"
    assert target_platforms(manifest) == ["linux", "macos", "windows"]


def test_snippet_package_provides_no_commands(tmp_path):
    # Not even the default entrypoint: a snippet package takes no part in the
    # shim system at all.
    assert commands_of(load_manifest(write_package(tmp_path))) == {}


def test_variants_are_derived_from_the_tree_not_the_manifest(tmp_path):
    package_dir = write_package(
        tmp_path, files=("src/args.sh", "src/args.py", "src/args.cpp", "README.md")
    )
    manifest = load_manifest(package_dir)

    # One [snippet.args] section, three languages, none of them written down.
    assert list(manifest.snippet) == ["args"]
    assert snippet_variants(manifest, package_dir) == {"args": ["cpp", "py", "sh"]}


def test_a_command_package_has_no_snippet_variants(tmp_path):
    package_dir = tmp_path / "my-tool"
    package_dir.mkdir()
    (package_dir / "meta.toml").write_text(
        '[package]\nnamespace = "acme"\nname = "my-tool"\nversion = "1.0.0"\n'
        'language = "python"\n\n[platforms]\nos = ["linux"]\n'
    )
    (package_dir / "src").mkdir()
    (package_dir / "src" / "main.py").write_text("print('hi')\n")

    # src/main.py is a script, not a snippet named "main".
    assert snippet_variants(load_manifest(package_dir), package_dir) == {}


def test_snippet_and_commands_are_mutually_exclusive():
    with pytest.raises(ManifestError, match="cannot also declare"):
        validate_manifest(
            {
                "package": {"namespace": "acme", "name": "b", "version": "1.0.0"},
                "snippet": {"args": {}},
                "commands": {"go": "src/go.sh"},
            }
        )


def test_snippet_package_may_not_declare_a_language():
    with pytest.raises(ManifestError, match="no \\[package\\] language"):
        validate_manifest(
            {
                "package": {
                    "namespace": "acme",
                    "name": "b",
                    "version": "1.0.0",
                    "language": "bash",
                },
                "snippet": {"args": {}},
            }
        )


def test_command_package_still_requires_language_and_platforms():
    with pytest.raises(ManifestError, match="language is required"):
        validate_manifest(
            {"package": {"namespace": "acme", "name": "b", "version": "1.0.0"}}
        )
    with pytest.raises(ManifestError, match="\\[platforms\\] is required"):
        validate_manifest(
            {
                "package": {
                    "namespace": "acme",
                    "name": "b",
                    "version": "1.0.0",
                    "language": "bash",
                }
            }
        )


def test_declared_snippet_with_no_source_file_is_an_error(tmp_path):
    with pytest.raises(ManifestError, match=r"\[snippet.args\] has no src/args.<ext>"):
        load_manifest(write_package(tmp_path, files=("README.md",)))


def test_undeclared_source_file_is_an_error(tmp_path):
    # The other direction, so a typo'd filename is caught at pack time rather
    # than surfacing as a snippet nobody can find.
    with pytest.raises(ManifestError, match=r"src/agrs.sh has no \[snippet.agrs\]"):
        load_manifest(write_package(tmp_path, files=("src/args.sh", "src/agrs.sh")))


def test_snippet_declares_a_platform_when_it_wants_one(tmp_path):
    manifest = load_manifest(
        write_package(
            tmp_path,
            manifest_text=SNIPPET_MANIFEST + '\n[platforms]\nos = ["linux", "macos"]\n',
        )
    )
    assert target_platforms(manifest) == ["linux", "macos"]


def test_language_names_imply_their_extension():
    # What lets `search --language python` reach a snippet labelled "py".
    assert extension_for_language("python") == "py"
    assert extension_for_language("cpp") is None
