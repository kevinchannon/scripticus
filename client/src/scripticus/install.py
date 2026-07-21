"""Local package installation (`scripticus install -f`).

The client-side install state lives under ``~/.scripticus/`` (overridable
via the ``SCRIPTICUS_HOME`` environment variable):

- ``installed.lock`` — the install-state file (JSON): every installed
  package with exact version, content hash, owned commands, and provenance.
- ``pkgs/<namespace>/<name>/<version>/`` — the installed package trees.
- ``bin/`` — the shim directory. Shims are one-line interpreter wrappers on
  POSIX and generated ``.cmd`` files on Windows (D11); the interpreter comes
  from the manifest's language field, so extraction losing the executable
  bit (zip) does not matter.

Every command materialises three shims (D38): the fully-qualified
``<namespace>.<package>.<command>`` (structurally unique, the real
interpreter wrapper), plus ``<namespace>.<command>`` and bare
``<command>`` conveniences that delegate directly to it. Only the
convenience tiers can collide; their ownership is tracked per entry in
the lockfile's ``shims`` list (``commands`` lists what the package
provides, which never changes with ownership).
"""

import json
import os
import shutil
import sys
import tarfile
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from scripticus_schema.manifest import LANGUAGES, Manifest, commands_of, load_manifest
from scripticus_schema.semver import semver_key
from scripticus_schema.treehash import tree_hash


class InstallError(Exception):
    """A package could not be installed."""


def scripticus_home() -> Path:
    return Path(os.environ.get("SCRIPTICUS_HOME") or Path.home() / ".scripticus")


def current_os() -> str:
    if sys.platform.startswith("linux"):
        return "linux"
    if sys.platform == "darwin":
        return "macos"
    if sys.platform in ("win32", "cygwin"):
        return "windows"
    return sys.platform


# --- Lockfile -------------------------------------------------------------


def read_lockfile(home: Path) -> dict:
    path = home / "installed.lock"
    if not path.is_file():
        return {"packages": []}
    return json.loads(path.read_text())


def write_lockfile(home: Path, lock: dict) -> None:
    path = home / "installed.lock"
    temporary = path.with_suffix(".lock.tmp")
    temporary.write_text(json.dumps(lock, indent=2) + "\n")
    os.replace(temporary, path)


def _find_entry(lock: dict, namespace: str, name: str) -> dict | None:
    for entry in lock["packages"]:
        if entry["namespace"] == namespace and entry["name"] == name:
            return entry
    return None


# --- Shim naming (D38) ------------------------------------------------------
#
# Dot count identifies a shim's tier — namespaces, package names, and
# command names all exclude '.'.


def fq_shim(namespace: str, name: str, command: str) -> str:
    return f"{namespace}.{name}.{command}"


def ns_shim(namespace: str, command: str) -> str:
    return f"{namespace}.{command}"


def convenience_shims(namespace: str, commands) -> list[str]:
    """Every convenience-tier shim name for a package's commands, sorted."""
    return sorted(
        {shim for command in commands for shim in (command, ns_shim(namespace, command))}
    )


def shim_command(shim: str) -> str:
    """The command a convenience shim name refers to (its last segment)."""
    return shim.rpartition(".")[2]


# --- Transaction preparation ----------------------------------------------


def resolve_dependencies(dependencies: dict[str, str]) -> None:
    """Resolver stub. Dependency resolution is server-side (D15) and no
    registry exists to resolve against yet, so a declared package dependency
    is an error rather than a silently absent dependency. Remote install
    replaces this with real resolution.
    """
    if dependencies:
        listed = ", ".join(
            f"{name} ({constraint})" for name, constraint in sorted(dependencies.items())
        )
        raise InstallError(
            f"cannot resolve package dependencies: {listed}"
            " — resolution needs a registry, so local installs currently"
            " support only dependency-free packages"
        )


@dataclass
class Tool:
    name: str
    found: bool


@dataclass
class Conflict:
    shim: str  # the contested convenience shim name (bare or ns.cmd)
    owner: str  # "namespace/name version"


@dataclass
class Transaction:
    source: Path
    staging: Path  # temp dir owning package_root; caller must clean up
    package_root: Path  # the extracted package tree
    manifest: Manifest
    content_hash: str
    action: str  # install | upgrade | downgrade | reinstall | already-installed
    installed_version: str | None
    commands: dict[str, str]
    required_tools: list[Tool] = field(default_factory=list)
    optional_tools: list[Tool] = field(default_factory=list)
    conflicts: list[Conflict] = field(default_factory=list)

    @property
    def package_id(self) -> str:
        package = self.manifest.package
        return f"{package.namespace}/{package.name}"

    @property
    def version(self) -> str:
        return self.manifest.package.version


def _extract_archive(archive: Path, destination: Path) -> Path:
    """Extract and return the single root directory of the package tree."""
    name = archive.name
    if name.endswith((".tar.gz", ".tgz")):
        with tarfile.open(archive) as tar:
            tar.extractall(destination, filter="data")
    elif name.endswith(".zip"):
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(destination)
    else:
        raise InstallError(f"'{archive}' is not a supported archive (.tar.gz or .zip)")

    roots = list(destination.iterdir())
    if len(roots) != 1 or not roots[0].is_dir():
        raise InstallError(f"'{archive}' does not contain a single package directory")
    return roots[0]


def prepare_install(archive: Path, home: Path) -> Transaction:
    """Extract, validate, and work out what installing ``archive`` would do.

    Touches nothing outside a temporary staging directory, which the caller
    must remove (``Transaction.staging``).
    """
    staging = Path(tempfile.mkdtemp(prefix="scripticus-install-"))
    try:
        package_root = _extract_archive(archive, staging)
        manifest = load_manifest(package_root)  # raises ManifestError

        os_list = manifest.platforms.os
        machine = current_os()
        if machine not in os_list:
            supported = ", ".join(os_list)
            raise InstallError(
                f"package supports [{supported}] but this machine is {machine}"
            )

        resolve_dependencies(dict(manifest.dependencies.packages))

        package = manifest.package
        lock = read_lockfile(home)
        entry = _find_entry(lock, package.namespace, package.name)
        installed_version = entry["version"] if entry else None

        if entry is None:
            action = "install"
        elif entry["version"] == package.version:
            content_hash = tree_hash(package_root)
            action = "already-installed" if entry["content_hash"] == content_hash else "reinstall"
        elif semver_key(package.version) > semver_key(entry["version"]):
            action = "upgrade"
        else:
            action = "downgrade"

        tools = manifest.dependencies.tools
        required_tools = [Tool(t, shutil.which(t) is not None) for t in tools.requires]
        optional_tools = [Tool(t, shutil.which(t) is not None) for t in tools.optional]

        commands = commands_of(manifest)
        incoming = convenience_shims(package.namespace, commands)
        conflicts = []
        for other in lock["packages"]:
            if entry is not None and other is entry:
                continue  # replacing our own shims is not a conflict
            owned = set(other.get("shims", []))
            for shim in incoming:
                if shim in owned:
                    conflicts.append(
                        Conflict(shim, f"{other['namespace']}/{other['name']} {other['version']}")
                    )

        return Transaction(
            source=archive,
            staging=staging,
            package_root=package_root,
            manifest=manifest,
            content_hash=tree_hash(package_root),
            action=action,
            installed_version=installed_version,
            commands=commands,
            required_tools=required_tools,
            optional_tools=optional_tools,
            conflicts=conflicts,
        )
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


# --- Applying the transaction ---------------------------------------------


def _shim_path(bin_dir: Path, command: str) -> Path:
    return bin_dir / (f"{command}.cmd" if os.name == "nt" else command)


def _write_shim(bin_dir: Path, command: str, script: Path, language: str) -> None:
    lang = LANGUAGES[language]
    shim = _shim_path(bin_dir, command)
    if os.name == "nt":
        shim.write_text(f'@echo off\r\n{lang.windows_interpreter} "{script}" %*\r\n')
    else:
        shim.write_text(f'#!/bin/sh\nexec {lang.interpreter} "{script}" "$@"\n')
        shim.chmod(0o755)


def _write_delegating_shim(bin_dir: Path, shim_name: str, target_name: str) -> None:
    """A convenience shim: one hop, straight to the fully-qualified shim
    (never convenience-to-convenience), so reading it names the true owner.
    """
    shim = _shim_path(bin_dir, shim_name)
    target = _shim_path(bin_dir, target_name)
    if os.name == "nt":
        shim.write_text(f'@echo off\r\ncall "{target}" %*\r\n')
    else:
        shim.write_text(f'#!/bin/sh\nexec "{target}" "$@"\n')
        shim.chmod(0o755)


def apply_install(transaction: Transaction, home: Path) -> None:
    """Install the staged package tree: files, shims, lockfile."""
    package = transaction.manifest.package
    namespace, name, version = package.namespace, package.name, package.version

    bin_dir = home / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)

    install_dir = home / "pkgs" / namespace / name / version
    if install_dir.exists():
        shutil.rmtree(install_dir)
    install_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(transaction.package_root, install_dir)

    lock = read_lockfile(home)
    previous = _find_entry(lock, namespace, name)
    if previous is not None:
        # Remove the previous version's tree and any shims (fully-qualified
        # or owned convenience) for commands the new version no longer
        # provides.
        if previous["version"] != version:
            shutil.rmtree(home / "pkgs" / namespace / name / previous["version"], ignore_errors=True)
        for command in previous["commands"]:
            if command not in transaction.commands:
                _shim_path(bin_dir, fq_shim(namespace, name, command)).unlink(missing_ok=True)
        for shim in previous.get("shims", []):
            if shim_command(shim) not in transaction.commands:
                _shim_path(bin_dir, shim).unlink(missing_ok=True)
        lock["packages"].remove(previous)

    for command, script in transaction.commands.items():
        target = fq_shim(namespace, name, command)
        _write_shim(bin_dir, target, install_dir / script, package.language)
        for shim in (command, ns_shim(namespace, command)):
            _write_delegating_shim(bin_dir, shim, target)

    # Convenience shims are last-install-wins (D11/D38): any contested one
    # now belongs to this package, so drop it from the previous owner.
    conflicted = {conflict.shim for conflict in transaction.conflicts}
    for entry in lock["packages"]:
        entry["shims"] = [s for s in entry.get("shims", []) if s not in conflicted]

    lock["packages"].append(
        {
            "namespace": namespace,
            "name": name,
            "version": version,
            "language": package.language,
            "content_hash": transaction.content_hash,
            "commands": sorted(transaction.commands),
            "shims": convenience_shims(namespace, transaction.commands),
            "direct": True,
            "provenance": {"type": "local", "source": str(transaction.source.resolve())},
            # Always empty until the resolver exists: resolve_dependencies
            # rejects any package that declares dependencies.
            "dependencies": {},
        }
    )
    lock["packages"].sort(key=lambda e: (e["namespace"], e["name"]))
    write_lockfile(home, lock)
