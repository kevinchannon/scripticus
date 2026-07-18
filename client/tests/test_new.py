import os
import tomllib

import pytest
from typer.testing import CliRunner

from scripticus.cli import app

runner = CliRunner()


@pytest.fixture
def in_tmp_path(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_new_bash_creates_documented_layout(in_tmp_path):
    result = runner.invoke(app, ["new", "bash", "my-cool-script"])
    assert result.exit_code == 0

    pkg = in_tmp_path / "my-cool-script"
    assert (pkg / "meta.toml").is_file()
    assert (pkg / "LICENSE").is_file()
    assert (pkg / "README.md").is_file()
    assert (pkg / "src" / "main.sh").is_file()
    assert (pkg / "test").is_dir()


def test_manifest_is_valid_toml_with_expected_fields(in_tmp_path):
    runner.invoke(app, ["new", "bash", "my-cool-script"])

    manifest = tomllib.loads((in_tmp_path / "my-cool-script" / "meta.toml").read_text())
    assert manifest["package"]["name"] == "my-cool-script"
    assert manifest["package"]["language"] == "bash"
    assert manifest["package"]["version"] == "0.1.0"
    assert manifest["package"]["namespace"] == ""
    assert manifest["platforms"]["os"] == ["linux", "macos"]


def test_python_package_gets_python_entrypoint(in_tmp_path):
    result = runner.invoke(app, ["new", "python", "my-tool"])
    assert result.exit_code == 0
    assert (in_tmp_path / "my-tool" / "src" / "main.py").is_file()


def test_powershell_package_gets_ps1_entrypoint_and_windows_platform(in_tmp_path):
    result = runner.invoke(app, ["new", "powershell", "my-tool"])
    assert result.exit_code == 0
    assert (in_tmp_path / "my-tool" / "src" / "main.ps1").is_file()

    manifest = tomllib.loads((in_tmp_path / "my-tool" / "meta.toml").read_text())
    assert manifest["platforms"]["os"] == ["windows"]


@pytest.mark.skipif(os.name == "nt", reason="no executable bit on Windows")
def test_posix_entrypoint_is_executable(in_tmp_path):
    runner.invoke(app, ["new", "bash", "my-cool-script"])
    mode = (in_tmp_path / "my-cool-script" / "src" / "main.sh").stat().st_mode
    assert mode & 0o111


def test_unknown_language_is_rejected(in_tmp_path):
    result = runner.invoke(app, ["new", "cobol", "my-cool-script"])
    assert result.exit_code != 0
    assert "unknown language" in result.output
    assert not (in_tmp_path / "my-cool-script").exists()


@pytest.mark.parametrize("bad_name", ["Has-Caps", "under_scores", "-leading-dash", "trailing-", "spa ce"])
def test_non_kebab_case_name_is_rejected(in_tmp_path, bad_name):
    result = runner.invoke(app, ["new", "bash", bad_name])
    assert result.exit_code != 0
    if not bad_name.startswith("-"):
        # A leading-dash name is rejected earlier, as an unknown option.
        assert "kebab-case" in result.output
    assert not (in_tmp_path / bad_name).exists()


def test_existing_directory_is_refused(in_tmp_path):
    (in_tmp_path / "my-cool-script").mkdir()
    result = runner.invoke(app, ["new", "bash", "my-cool-script"])
    assert result.exit_code == 1
    assert "already exists" in result.output
