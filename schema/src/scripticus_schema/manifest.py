"""The package manifest (meta.toml): Pydantic schema and validation (D13).

The client validates for UX at pack/install time; the server re-validates
authoritatively at publish (D8). Both use this module, so there is exactly
one definition of a valid manifest (D29).
"""

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError, field_validator

from scripticus_common.semver import SEMVER_RE

# Package names are kebab-case (enforced again at publish; validated
# client-side so authors find out before they have written any code).
PACKAGE_NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

# Lower-case letters, digits, and dashes, starting with a letter. Stricter
# than Gitea's own username rules (which also allow '.', '_', and upper
# case): a namespace must satisfy both.
NAMESPACE_RE = re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$")

# System-tool names come from third-party manifests and later reach a shell
# command (D44's operator-configured installer), so they are constrained to a
# safe charset at parse time — no whitespace, quotes, or shell metacharacters
# — and shell-quoted again at invocation. A manifest cannot inject shell.
TOOL_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")

KNOWN_OS = ("linux", "macos", "windows")

# One archive per format group: POSIX/macOS targets travel as .tar.gz,
# Windows as .zip (D26). The client packs by this table; the server checks
# at publish that an uploaded archive's format matches the manifest's
# declared platforms.
FORMAT_GROUPS = (("tar.gz", ("linux", "macos")), ("zip", ("windows",)))


@dataclass(frozen=True)
class Language:
    extension: str
    interpreter: str
    windows_interpreter: str


LANGUAGES: dict[str, Language] = {
    "bash": Language("sh", "bash", "bash"),
    "python": Language("py", "python3", "python"),
    "powershell": Language("ps1", "pwsh", "powershell"),
}


class ManifestError(Exception):
    """A package directory does not contain a valid manifest."""


class PackageMeta(BaseModel):
    namespace: str
    name: str
    version: str
    language: str
    description: str = ""

    @field_validator("namespace")
    @classmethod
    def _check_namespace(cls, value: str) -> str:
        if not NAMESPACE_RE.match(value):
            raise ValueError(
                f"'{value}' is not a valid namespace"
                " (lower-case letters, digits, and dashes, starting with a letter)"
            )
        return value

    @field_validator("name")
    @classmethod
    def _check_name(cls, value: str) -> str:
        if not PACKAGE_NAME_RE.match(value):
            raise ValueError(f"'{value}' is not kebab-case")
        return value

    @field_validator("version")
    @classmethod
    def _check_version(cls, value: str) -> str:
        if not SEMVER_RE.match(value):
            raise ValueError(f"'{value}' is not strict semver")
        return value

    @field_validator("language")
    @classmethod
    def _check_language(cls, value: str) -> str:
        if value not in LANGUAGES:
            supported = ", ".join(sorted(LANGUAGES))
            raise ValueError(f"'{value}' is not supported ({supported})")
        return value


class Platforms(BaseModel):
    os: list[str]

    @field_validator("os")
    @classmethod
    def _check_os(cls, value: list[str]) -> list[str]:
        if not value or not all(os_name in KNOWN_OS for os_name in value):
            known = ", ".join(KNOWN_OS)
            raise ValueError(f"os must be a non-empty list drawn from: {known}")
        return value


class ToolDependencies(BaseModel):
    requires: list[str] = Field(default_factory=list)
    optional: list[str] = Field(default_factory=list)

    @field_validator("requires", "optional")
    @classmethod
    def _check_tool_names(cls, value: list[str]) -> list[str]:
        for name in value:
            if not TOOL_NAME_RE.match(name):
                raise ValueError(
                    f"'{name}' is not a valid tool name"
                    " (letters, digits, and . _ + -, not starting with . _ + -)"
                )
        return value


class Dependencies(BaseModel):
    packages: dict[str, str] = Field(default_factory=dict)
    tools: ToolDependencies = Field(default_factory=ToolDependencies)


class Manifest(BaseModel):
    package: PackageMeta
    platforms: Platforms
    commands: dict[str, str] | None = None
    dependencies: Dependencies = Field(default_factory=Dependencies)


def _format_error(error: dict) -> str:
    location = ".".join(str(part) for part in error["loc"])
    message = error["msg"]
    message = message.removeprefix("Value error, ")
    return f"[{location}] {message}" if location else message


def validate_manifest(data: dict) -> Manifest:
    """Validate parsed manifest data; raise ManifestError listing every problem."""
    try:
        return Manifest.model_validate(data)
    except ValidationError as exc:
        raise ManifestError(
            "\n".join(_format_error(error) for error in exc.errors())
        ) from exc


def check_package_tree(manifest: Manifest, package_dir: Path) -> list[str]:
    """Checks that need the package tree, not just the manifest: command
    targets must exist inside the package, and without a [commands] table
    the default entrypoint must exist. Used by pack/install client-side and
    by the server at publish, which validates the extracted tree the same
    way.
    """
    errors = []
    if manifest.commands is None:
        entrypoint = f"src/main.{LANGUAGES[manifest.package.language].extension}"
        if not (package_dir / entrypoint).is_file():
            errors.append(f"no [commands] table and no {entrypoint}")
    else:
        for command, script in manifest.commands.items():
            script_path = package_dir / script
            if not script_path.is_file():
                errors.append(f"[commands] {command} points at missing file '{script}'")
            elif package_dir.resolve() not in script_path.resolve().parents:
                errors.append(f"[commands] {command} points outside the package: '{script}'")
    return errors


def load_manifest(package_dir: Path) -> Manifest:
    """Read and validate ``meta.toml``; raise ManifestError listing every problem."""
    manifest_path = package_dir / "meta.toml"
    if not manifest_path.is_file():
        raise ManifestError(f"no meta.toml found in '{package_dir}'")

    try:
        data = tomllib.loads(manifest_path.read_text())
    except tomllib.TOMLDecodeError as exc:
        raise ManifestError(f"meta.toml is not valid TOML: {exc}") from exc

    try:
        manifest = Manifest.model_validate(data)
        errors = check_package_tree(manifest, package_dir)
    except ValidationError as exc:
        manifest = None
        errors = [_format_error(error) for error in exc.errors()]

    if errors:
        problems = "\n".join(f"  - {error}" for error in errors)
        raise ManifestError(f"'{package_dir}' is not a valid package:\n{problems}")
    return manifest


def commands_of(manifest: Manifest) -> dict[str, str]:
    """The command -> script-path map, applying the default-entrypoint rule."""
    if manifest.commands:
        return dict(manifest.commands)
    extension = LANGUAGES[manifest.package.language].extension
    return {manifest.package.name: f"src/main.{extension}"}
