from pathlib import Path

import pytest

from scripticus_schema.manifest import (
    Manifest,
    ManifestError,
    commands_of,
    load_manifest,
    validate_manifest,
)

VALID_MANIFEST = """\
[package]
namespace = "acme"
name = "my-tool"
version = "1.2.3"
language = "python"
description = "A tool"

[platforms]
os = ["linux", "macos"]
"""


def write_package(tmp_path: Path, manifest_text: str = VALID_MANIFEST, files=("src/main.py",)) -> Path:
    package_dir = tmp_path / "my-tool"
    package_dir.mkdir()
    (package_dir / "meta.toml").write_text(manifest_text)
    for name in files:
        path = package_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("print('hi')\n")
    return package_dir


def test_valid_manifest_loads(tmp_path):
    manifest = load_manifest(write_package(tmp_path))
    assert manifest.package.namespace == "acme"
    assert manifest.package.name == "my-tool"
    assert manifest.package.version == "1.2.3"
    assert manifest.platforms.os == ["linux", "macos"]
    assert manifest.commands is None
    assert manifest.dependencies.packages == {}
    assert manifest.dependencies.tools.requires == []


def test_missing_manifest_file(tmp_path):
    (tmp_path / "empty").mkdir()
    with pytest.raises(ManifestError, match="no meta.toml"):
        load_manifest(tmp_path / "empty")


def test_invalid_toml(tmp_path):
    package_dir = tmp_path / "pkg"
    package_dir.mkdir()
    (package_dir / "meta.toml").write_text("not [valid toml")
    with pytest.raises(ManifestError, match="not valid TOML"):
        load_manifest(package_dir)


@pytest.mark.parametrize(
    ("original", "replacement", "message"),
    [
        ('namespace = "acme"', 'namespace = "Bad_Namespace"', "not a valid namespace"),
        ('name = "my-tool"', 'name = "MyTool"', "not kebab-case"),
        ('version = "1.2.3"', 'version = "1.2"', "not strict semver"),
        ('language = "python"', 'language = "rust"', "not supported"),
        ('os = ["linux", "macos"]', 'os = ["linux", "amiga"]', "non-empty list drawn from"),
        ('os = ["linux", "macos"]', "os = []", "non-empty list drawn from"),
    ],
)
def test_invalid_fields_are_reported(tmp_path, original, replacement, message):
    text = VALID_MANIFEST.replace(original, replacement)
    with pytest.raises(ManifestError, match=message):
        load_manifest(write_package(tmp_path, text))


def test_all_problems_are_listed_at_once(tmp_path):
    text = VALID_MANIFEST.replace('name = "my-tool"', 'name = "MyTool"').replace(
        'version = "1.2.3"', 'version = "1.2"'
    )
    with pytest.raises(ManifestError) as excinfo:
        load_manifest(write_package(tmp_path, text))
    assert "not kebab-case" in str(excinfo.value)
    assert "not strict semver" in str(excinfo.value)


def test_missing_default_entrypoint(tmp_path):
    package_dir = write_package(tmp_path, files=())
    with pytest.raises(ManifestError, match="no \\[commands\\] table and no src/main.py"):
        load_manifest(package_dir)


def test_command_pointing_at_missing_file(tmp_path):
    text = VALID_MANIFEST + '\n[commands]\ntool = "src/nonexistent.py"\n'
    with pytest.raises(ManifestError, match="missing file 'src/nonexistent.py'"):
        load_manifest(write_package(tmp_path, text))


def test_command_pointing_outside_the_package(tmp_path):
    (tmp_path / "outside.py").write_text("print('hi')\n")
    text = VALID_MANIFEST + '\n[commands]\ntool = "../outside.py"\n'
    with pytest.raises(ManifestError, match="outside the package"):
        load_manifest(write_package(tmp_path, text))


def test_validate_manifest_needs_no_filesystem():
    manifest = validate_manifest(
        {
            "package": {
                "namespace": "acme",
                "name": "my-tool",
                "version": "1.2.3",
                "language": "bash",
            },
            "platforms": {"os": ["linux"]},
        }
    )
    assert isinstance(manifest, Manifest)


def test_commands_of_applies_default_entrypoint(tmp_path):
    manifest = load_manifest(write_package(tmp_path))
    assert commands_of(manifest) == {"my-tool": "src/main.py"}


def test_commands_of_uses_explicit_table(tmp_path):
    text = VALID_MANIFEST + '\n[commands]\nfoo = "src/main.py"\n'
    manifest = load_manifest(write_package(tmp_path, text))
    assert commands_of(manifest) == {"foo": "src/main.py"}


def test_dependencies_are_parsed(tmp_path):
    text = (
        VALID_MANIFEST
        + '\n[dependencies.packages]\n"infra/log-common" = "^2.0"\n'
        + '\n[dependencies.tools]\nrequires = ["git"]\noptional = ["fzf"]\n'
    )
    manifest = load_manifest(write_package(tmp_path, text))
    assert manifest.dependencies.packages == {"infra/log-common": "^2.0"}
    assert manifest.dependencies.tools.requires == ["git"]
    assert manifest.dependencies.tools.optional == ["fzf"]


@pytest.mark.parametrize(
    "name",
    ["git", "gcc-12", "python3.11", "libssl-dev", "a", "c++", "g++", "clang_format"],
)
def test_valid_tool_names_accepted(name):
    Manifest.model_validate(
        {
            "package": {
                "namespace": "acme",
                "name": "my-tool",
                "version": "1.2.3",
                "language": "python",
            },
            "platforms": {"os": ["linux"]},
            "dependencies": {"tools": {"requires": [name]}},
        }
    )


@pytest.mark.parametrize(
    "name",
    [
        "git; rm -rf /",  # shell metacharacters
        "git rm",  # whitespace
        "$(whoami)",  # command substitution
        "-flag",  # leading dash
        ".hidden",  # leading dot
        "+plus",  # leading plus
        "",  # empty
        "foo/bar",  # slash
    ],
)
def test_injection_shaped_tool_names_rejected(tmp_path, name):
    text = VALID_MANIFEST + f'\n[dependencies.tools]\nrequires = ["{name}"]\n'
    with pytest.raises(ManifestError, match="not a valid tool name"):
        load_manifest(write_package(tmp_path, text))


def test_tool_name_with_quote_rejected():
    # A quote can't survive TOML parsing to reach the validator, so check the
    # model directly: the charset excludes it regardless of how it arrives.
    with pytest.raises(ValueError, match="not a valid tool name"):
        Manifest.model_validate(
            {
                "package": {
                    "namespace": "acme",
                    "name": "my-tool",
                    "version": "1.2.3",
                    "language": "python",
                },
                "platforms": {"os": ["linux"]},
                "dependencies": {"tools": {"requires": ['git"; evil']}},
            }
        )


def test_optional_tool_names_validated_too(tmp_path):
    text = VALID_MANIFEST + '\n[dependencies.tools]\noptional = ["bad tool"]\n'
    with pytest.raises(ManifestError, match="not a valid tool name"):
        load_manifest(write_package(tmp_path, text))
