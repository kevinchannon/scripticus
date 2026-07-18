import typer
from rich.console import Console

from scripticus_server import __version__

app = typer.Typer(no_args_is_help=True)
console = Console()


def _print_version(value: bool) -> None:
    if value:
        console.print(f"scripticus-svr {__version__}")
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
    """Scripticus index service — search, resolution, and publishing for shared scripts."""
