"""The package manifest (meta.toml): schema constants and validation.

Client-side validation is for UX only; the server will re-validate
authoritatively (D8). This module is the natural seed of the future
``shared/`` schema package (D13).
"""

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

# Package names are kebab-case (enforced again at publish; validated here so
# authors find out before they have written any code).
PACKAGE_NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

# Lower-case letters, digits, and dashes, starting with a letter. Stricter
# than Gitea's own username rules (which also allow '.', '_', and upper
# case): a namespace must satisfy both.
NAMESPACE_RE = re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$")

# The official semver.org regex.
SEMVER_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)"
    r"(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?"
    r"(?:\+([0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?$"
)

KNOWN_OS = ("linux", "macos", "windows")


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


def load_manifest(package_dir: Path) -> dict:
    """Read and validate ``meta.toml``; raise ManifestError listing every problem."""
    manifest_path = package_dir / "meta.toml"
    if not manifest_path.is_file():
        raise ManifestError(f"no meta.toml found in '{package_dir}'")

    try:
        manifest = tomllib.loads(manifest_path.read_text())
    except tomllib.TOMLDecodeError as exc:
        raise ManifestError(f"meta.toml is not valid TOML: {exc}") from exc

    errors = []
    package = manifest.get("package", {})

    namespace = package.get("namespace", "")
    if not NAMESPACE_RE.match(namespace):
        errors.append(
            f"[package] namespace '{namespace}' is not valid"
            " (lower-case letters, digits, and dashes, starting with a letter)"
        )

    name = package.get("name", "")
    if not PACKAGE_NAME_RE.match(name):
        errors.append(f"[package] name '{name}' is not kebab-case")

    version = package.get("version", "")
    if not SEMVER_RE.match(version):
        errors.append(f"[package] version '{version}' is not strict semver")

    language = package.get("language", "")
    if language not in LANGUAGES:
        supported = ", ".join(sorted(LANGUAGES))
        errors.append(f"[package] language '{language}' is not supported ({supported})")

    os_list = manifest.get("platforms", {}).get("os", [])
    if not os_list or not all(os_name in KNOWN_OS for os_name in os_list):
        known = ", ".join(KNOWN_OS)
        errors.append(f"[platforms] os must be a non-empty list drawn from: {known}")

    commands = manifest.get("commands")
    if commands is None:
        # Entrypoint rule: without [commands], src/main.<ext> must exist.
        if language in LANGUAGES:
            entrypoint = f"src/main.{LANGUAGES[language].extension}"
            if not (package_dir / entrypoint).is_file():
                errors.append(f"no [commands] table and no {entrypoint}")
    else:
        for command, script in commands.items():
            script_path = package_dir / script
            if not script_path.is_file():
                errors.append(f"[commands] {command} points at missing file '{script}'")
            elif package_dir.resolve() not in script_path.resolve().parents:
                errors.append(f"[commands] {command} points outside the package: '{script}'")

    if errors:
        problems = "\n".join(f"  - {error}" for error in errors)
        raise ManifestError(f"'{package_dir}' is not a valid package:\n{problems}")

    return manifest


def commands_of(manifest: dict) -> dict[str, str]:
    """The command -> script-path map, applying the default-entrypoint rule."""
    commands = manifest.get("commands")
    if commands:
        return dict(commands)
    package = manifest["package"]
    extension = LANGUAGES[package["language"]].extension
    return {package["name"]: f"src/main.{extension}"}
