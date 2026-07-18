"""Archiving a package directory for distribution (`scripticus pack`)."""

import re
import tarfile
import tomllib
import zipfile
from pathlib import Path

from scripticus.scaffold import LANGUAGES, NAMESPACE_RE

# Kebab-case, as enforced for package names throughout.
NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

# The official semver.org regex.
SEMVER_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)"
    r"(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?"
    r"(?:\+([0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?$"
)

KNOWN_OS = ("linux", "macos", "windows")

# Junk that must never end up inside a distributed artifact.
EXCLUDED_NAMES = {".git", "__pycache__", ".DS_Store"}


class PackError(Exception):
    """A package directory could not be packed."""


def load_manifest(package_dir: Path) -> dict:
    """Read and validate ``meta.toml``; raise PackError listing every problem."""
    manifest_path = package_dir / "meta.toml"
    if not manifest_path.is_file():
        raise PackError(f"no meta.toml found in '{package_dir}'")

    try:
        manifest = tomllib.loads(manifest_path.read_text())
    except tomllib.TOMLDecodeError as exc:
        raise PackError(f"meta.toml is not valid TOML: {exc}") from exc

    errors = []
    package = manifest.get("package", {})

    namespace = package.get("namespace", "")
    if not NAMESPACE_RE.match(namespace):
        errors.append(
            f"[package] namespace '{namespace}' is not valid"
            " (lower-case letters, digits, and dashes, starting with a letter)"
        )

    name = package.get("name", "")
    if not NAME_RE.match(name):
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
        raise PackError(f"'{package_dir}' is not a valid package:\n{problems}")

    return manifest


# One archive per format group: POSIX/macOS targets travel as .tar.gz,
# Windows as .zip. A package targeting both produces one archive of each.
FORMAT_GROUPS = (("tar.gz", ("linux", "macos")), ("zip", ("windows",)))


def archive_filenames(manifest: dict) -> list[str]:
    """Wheel-style structured tags: name-version-platforms-language.<ext>.

    Dashes within name/version are normalised to underscores so the dash is
    an unambiguous field separator; multiple target OSes within one archive
    are joined with dots (in canonical order). One filename per format group
    the package targets. The filename is human-legible redundancy only — the
    manifest inside the archive is the source of truth.
    """
    package = manifest["package"]
    name = package["name"].replace("-", "_")
    version = package["version"].replace("-", "_")
    os_list = manifest["platforms"]["os"]
    filenames = []
    for extension, group in FORMAT_GROUPS:
        targets = [os_name for os_name in group if os_name in os_list]
        if targets:
            platform_tag = ".".join(targets)
            filenames.append(
                f"{name}-{version}-{platform_tag}-{package['language']}.{extension}"
            )
    return filenames


def _iter_package_paths(package_dir: Path):
    for path in sorted(package_dir.rglob("*")):
        relative = path.relative_to(package_dir)
        if any(part in EXCLUDED_NAMES for part in relative.parts):
            continue
        yield path, relative


def pack_package(package_dir: Path, output_dir: Path) -> list[Path]:
    """Validate ``package_dir`` and write its archive(s) into ``output_dir``.

    Returns the paths of the archives written — one per format group the
    package targets. The archive root is the package name from the manifest,
    regardless of the directory's on-disk name.
    """
    manifest = load_manifest(package_dir)
    root = manifest["package"]["name"]

    output_dir.mkdir(parents=True, exist_ok=True)

    archive_paths = []
    for filename in archive_filenames(manifest):
        archive_path = output_dir / filename
        if archive_path.suffix == ".zip":
            with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
                for path, relative in _iter_package_paths(package_dir):
                    if path.is_dir():
                        archive.writestr(f"{root}/{relative}/", "")
                    else:
                        archive.write(path, f"{root}/{relative}")
        else:
            with tarfile.open(archive_path, "w:gz") as archive:
                for path, relative in _iter_package_paths(package_dir):
                    archive.add(path, arcname=f"{root}/{relative}", recursive=False)
        archive_paths.append(archive_path)

    return archive_paths
