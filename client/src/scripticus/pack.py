"""Archiving a package directory for distribution (`scripticus pack`)."""

import tarfile
import zipfile
from pathlib import Path

from scripticus.manifest import load_manifest

# Junk that must never end up inside a distributed artifact.
EXCLUDED_NAMES = {".git", "__pycache__", ".DS_Store"}

# One archive per format group: POSIX/macOS targets travel as .tar.gz,
# Windows as .zip. A package targeting both produces one archive of each
# (D26).
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
