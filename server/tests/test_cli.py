from typer.testing import CliRunner

from scripticus_server import __version__
from scripticus_server.cli import app

runner = CliRunner()


def test_version_long_flag_prints_version_and_exits_cleanly():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert f"scripticus-svr {__version__}" in result.output


def test_version_short_flag_prints_version_and_exits_cleanly():
    result = runner.invoke(app, ["-v"])
    assert result.exit_code == 0
    assert f"scripticus-svr {__version__}" in result.output


def test_bare_invocation_shows_help():
    result = runner.invoke(app, [])
    # no_args_is_help exits with code 2: no arguments is incomplete input.
    assert result.exit_code == 2
    assert "Usage" in result.output
