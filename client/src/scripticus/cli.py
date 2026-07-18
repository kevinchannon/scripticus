from pathlib import Path

import typer
from rich.console import Console

from scripticus import __version__, scaffold
from scripticus.pack import PackError, pack_package

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
    except PackError as exc:
        console.print(f"[red]error:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    console.print(f"Packed [bold]{package_dir.name}[/bold]:")
    for archive_path in archive_paths:
        console.print(f"  {archive_path}")
