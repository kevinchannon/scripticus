import shutil
from enum import Enum
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

from scripticus import __version__, scaffold
from scripticus.install import (
    InstallError,
    Transaction,
    apply_install,
    prepare_install,
    scripticus_home,
)
from scripticus.manifest import ManifestError
from scripticus.pack import pack_package

app = typer.Typer(no_args_is_help=True)
console = Console()


def _print_version(value: bool) -> None:
    if value:
        console.print(f"scripticus {__version__}")
        raise typer.Exit()


@app.callback(invoke_without_command=True)
def main(
    version: bool = typer.Option(
        False,
        "--version",
        "-v",
        callback=_print_version,
        is_eager=True,
        help="Show the version and exit.",
    ),
) -> None:
    """Scripticus — publish, discover, version, and install shared scripts."""


def _validate_language(value: str) -> str:
    if value not in scaffold.LANGUAGES:
        supported = ", ".join(sorted(scaffold.LANGUAGES))
        raise typer.BadParameter(f"unknown language '{value}' (supported: {supported})")
    return value


def _validate_package_name(value: str) -> str:
    if not scaffold.PACKAGE_NAME_RE.match(value):
        raise typer.BadParameter(
            f"'{value}' is not a valid package name"
            " (names are kebab-case: lower-case letters, digits, and dashes)"
        )
    return value


def _validate_namespace(value: str) -> str:
    if not scaffold.NAMESPACE_RE.match(value):
        raise typer.BadParameter(
            f"'{value}' is not a valid namespace"
            " (lower-case letters, digits, and dashes, starting with a letter)"
        )
    return value


@app.command()
def new(
    language: str = typer.Argument(
        ...,
        callback=_validate_language,
        help="Language of the new package (bash, powershell, python).",
    ),
    name: str = typer.Argument(
        ...,
        callback=_validate_package_name,
        help="Package name (kebab-case).",
    ),
    namespace: str = typer.Option(
        ...,
        "--namespace",
        "-n",
        callback=_validate_namespace,
        help="Publishing namespace (a Gitea user or organisation).",
    ),
) -> None:
    """Scaffold a new package directory."""
    cwd = Path.cwd()
    try:
        created = scaffold.scaffold_package(language, name, namespace, cwd)
    except scaffold.ScaffoldError as exc:
        console.print(f"[red]error:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    console.print(f"Created package [bold]{name}[/bold]:")
    for path in created:
        suffix = "/" if path.is_dir() else ""
        console.print(f"  {path.relative_to(cwd)}{suffix}")


@app.command()
def pack(
    package_dir: Path = typer.Argument(
        ...,
        exists=True,
        file_okay=False,
        help="Path to the package directory to archive.",
    ),
    output: Path = typer.Option(
        Path("."),
        "--output",
        "-o",
        file_okay=False,
        help="Directory to place the archive in (created if needed).",
    ),
) -> None:
    """Archive a package directory into a distributable artifact."""
    try:
        archive_paths = pack_package(package_dir, output)
    except ManifestError as exc:
        console.print(f"[red]error:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    console.print(f"Packed [bold]{package_dir.name}[/bold]:")
    for archive_path in archive_paths:
        console.print(f"  {archive_path}")


class ForceMode(str, Enum):
    NO_CONFLICTS = "no-conflicts"
    ALL = "all"


def _print_transaction(transaction: Transaction) -> None:
    console.print(
        f"Installing [bold]{transaction.package_id}[/bold] {transaction.version}"
        f" (from {transaction.source})"
    )

    if transaction.action == "install":
        console.print("\nNew packages:")
        commands = ", ".join(sorted(transaction.commands))
        console.print(f"  {transaction.package_id}  {transaction.version}  (commands: {commands})")
    elif transaction.action == "reinstall":
        console.print(
            f"\nReinstalling {transaction.package_id} {transaction.version}"
            " (contents differ from the installed copy)"
        )
    else:
        marker = "  (downgrade!)" if transaction.action == "downgrade" else ""
        console.print("\nVersion changes:")
        console.print(
            f"  {transaction.package_id}  {transaction.installed_version}"
            f" -> {transaction.version}{marker}"
        )

    def tool_line(kind: str, tools, missing_note: str) -> None:
        if tools:
            names = ", ".join(t.name for t in tools)
            missing = [t.name for t in tools if not t.found]
            status = f"[NOT FOUND: {', '.join(missing)}{missing_note}]" if missing else "[found]"
            console.print(f"{kind} system tools: {names}        {status}")

    console.print()
    tool_line("Required", transaction.required_tools, "")
    tool_line("Optional", transaction.optional_tools, " — some features degraded")

    if transaction.unresolved_deps:
        console.print("Package dependencies (not resolved for local installs):")
        for dep, constraint in transaction.unresolved_deps.items():
            console.print(f"  {dep}  {constraint}")

    if transaction.conflicts:
        console.print("\nShim conflicts:")
        for conflict in transaction.conflicts:
            console.print(
                f"  {conflict.command}  currently owned by {conflict.owner}"
                " — will be overwritten"
            )


@app.command()
def install(
    file: Path = typer.Option(
        ...,
        "--file",
        "-f",
        exists=True,
        dir_okay=False,
        help="Install from a local package archive (required until remote installs land).",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Auto-accept the transaction, but abort entirely on any shim conflict"
        " (same as --force no-conflicts).",
    ),
    force: Optional[ForceMode] = typer.Option(
        None,
        "--force",
        help="no-conflicts: auto-accept but abort on shim conflicts."
        " all: auto-accept everything, reporting each overwritten shim.",
    ),
) -> None:
    """Install a package from a local archive."""
    home = scripticus_home()
    mode = force.value if force else ("no-conflicts" if yes else None)

    try:
        transaction = prepare_install(file, home)
    except (InstallError, ManifestError) as exc:
        console.print(f"[red]error:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    try:
        if transaction.action == "already-installed":
            console.print(
                f"{transaction.package_id} {transaction.version} is already installed"
                " — nothing to do"
            )
            return

        _print_transaction(transaction)

        if mode is None:
            console.print()
            if not typer.confirm("Proceed?"):
                console.print("Aborted — nothing installed.")
                raise typer.Exit(code=1)
        elif mode == "no-conflicts" and transaction.conflicts:
            console.print(
                "\n[red]error:[/red] aborting: the transaction would overwrite existing"
                " command shims (nothing installed; use --force all to overwrite)"
            )
            raise typer.Exit(code=1)

        apply_install(transaction, home)

        console.print(f"\nInstalled [bold]{transaction.package_id}[/bold] {transaction.version}")
        if transaction.conflicts:
            console.print("Overwritten shims:")
            for conflict in transaction.conflicts:
                console.print(f"  {conflict.command}  (was {conflict.owner})")
    finally:
        shutil.rmtree(transaction.staging, ignore_errors=True)
