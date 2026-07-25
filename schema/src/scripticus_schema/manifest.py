"""The package manifest (meta.toml): Pydantic schema and validation (D13).

The client validates for UX at pack/install time; the server re-validates
authoritatively at publish (D8). Both use this module, so there is exactly
one definition of a valid manifest (D29).
"""

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from scripticus_common.snippet_variants import variants_from_paths

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

# A snippet package has no language of its own — its variants each carry one as
# a label, and it never runs (D58). Where the surrounding machinery needs *a*
# language string (the wheel-style archive tag, the index's artifact row), it
# gets this sentinel rather than a nullable column.
SNIPPET_LANGUAGE = "snippet"

# ...and usually no platform either: an omitted [platforms] means "any", the
# spelling pip's pure-Python wheels use. It still packs to both format groups
# (D26), so every OS gets its native container.
ANY_PLATFORM = "any"

# Snippet names reach the CLI as the token before the extension (`snip
# args.sh`), so they are kebab-case like package names — no dots (the extension
# separator), no slashes or colons (the qualified-reference separators).
SNIPPET_NAME_RE = PACKAGE_NAME_RE


def extension_for_language(language: str) -> str | None:
    """The file extension a language name implies, if it is a known one.

    Lets a language *name* select extension-labelled snippet variants, so
    ``search --language python`` finds ``args.py`` (D58) without the snippet
    side needing a language table of its own.
    """
    known = LANGUAGES.get(language)
    return known.extension if known else None


class ManifestError(Exception):
    """A package directory does not contain a valid manifest."""


class PackageMeta(BaseModel):
    namespace: str
    name: str
    version: str
    # Optional only because a snippet package has none (D58); required for
    # every other kind, enforced by Manifest's kind check below.
    language: str | None = None
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
    def _check_language(cls, value: str | None) -> str | None:
        if value is None:
            return None
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


class Snippet(BaseModel):
    """One ``[snippet.<name>]`` section: authored intent, nothing derivable.

    The description is the snippet's language-agnostic *purpose*, shared by
    every language variant of it — the code itself lives in ``src/<name>.<ext>``
    files, and which languages exist is derived from the tree, never authored
    (D58).
    """

    description: str = ""


class Manifest(BaseModel):
    package: PackageMeta
    # Both optional only for the snippet kind (D58); _check_kind requires them
    # of everything else.
    platforms: Platforms | None = None
    commands: dict[str, str] | None = None
    snippet: dict[str, Snippet] | None = None
    dependencies: Dependencies = Field(default_factory=Dependencies)

    @model_validator(mode="after")
    def _check_kind(self) -> "Manifest":
        """The package kinds are exclusive, and each requires its own fields.

        A snippet package is never run or sourced, so it has neither a language
        nor (usually) a platform; a command package must declare both.
        """
        if self.snippet is not None:
            if self.commands is not None:
                raise ValueError(
                    "a [snippet] package cannot also declare [commands]"
                    " — a package is one kind or the other"
                )
            if not self.snippet:
                raise ValueError("[snippet] is empty: declare at least one [snippet.<name>]")
            for name in self.snippet:
                if not SNIPPET_NAME_RE.match(name):
                    raise ValueError(f"snippet name '{name}' is not kebab-case")
            if self.package.language is not None:
                raise ValueError(
                    "a snippet package has no [package] language — a snippet is"
                    " never run, and each variant's language is its file extension"
                )
        else:
            if self.package.language is None:
                raise ValueError("[package] language is required")
            if self.platforms is None:
                raise ValueError("[platforms] is required")
        return self


def is_snippet(manifest: Manifest) -> bool:
    return manifest.snippet is not None


def language_tag(manifest: Manifest) -> str:
    """The language string the archive tag and index carry for this package."""
    return manifest.package.language or SNIPPET_LANGUAGE


def target_platforms(manifest: Manifest) -> list[str]:
    """The OSes this package targets — every known one when it declares none
    (the ``any`` case, D58), so platform filtering and the install-time
    platform check need no special case.
    """
    return list(manifest.platforms.os) if manifest.platforms else list(KNOWN_OS)


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


def _tree_relative_paths(package_dir: Path) -> list[str]:
    return [
        path.relative_to(package_dir).as_posix()
        for path in package_dir.rglob("*")
        if path.is_file()
    ]


def snippet_variants(manifest: Manifest, package_dir: Path) -> dict[str, list[str]]:
    """The snippet name -> sorted extensions map derived from a package tree.

    The author enumerates no languages: they are read off the ``src/<name>.<ext>``
    files by the shared rule (D51/D58), the same way client-side ``snip`` and
    server-side publish both derive them. Empty for any other kind of package,
    whose ``src/`` holds scripts, not snippets.
    """
    if manifest.snippet is None:
        return {}
    return variants_from_paths(_tree_relative_paths(package_dir))


def check_package_tree(manifest: Manifest, package_dir: Path) -> list[str]:
    """Checks that need the package tree, not just the manifest: command
    targets must exist inside the package, and without a [commands] table
    the default entrypoint must exist. Used by pack/install client-side and
    by the server at publish, which validates the extracted tree the same
    way.

    For a snippet package the check is that the manifest and ``src/`` agree in
    both directions: every declared snippet has at least one variant file, and
    every ``src/<name>.<ext>`` file is declared. Nothing checks that a snippet
    is *valid* in its claimed language — that stays the author's problem (D14).
    """
    errors = []
    if manifest.snippet is not None:
        found = snippet_variants(manifest, package_dir)
        for name in manifest.snippet:
            if name not in found:
                errors.append(f"[snippet.{name}] has no src/{name}.<ext> file")
        for name in found:
            if name not in manifest.snippet:
                extensions = ", ".join(f"src/{name}.{ext}" for ext in found[name])
                errors.append(f"{extensions} has no [snippet.{name}] section")
        return errors
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
    """The command -> script-path map, applying the default-entrypoint rule.

    Empty for a snippet package: it provides nothing runnable, so it takes no
    part in the shim system at all (D58).
    """
    if manifest.snippet is not None:
        return {}
    if manifest.commands:
        return dict(manifest.commands)
    extension = LANGUAGES[manifest.package.language].extension
    return {manifest.package.name: f"src/main.{extension}"}
