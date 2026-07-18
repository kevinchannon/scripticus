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


def new(*args: str):
    return runner.invoke(app, ["new", *args])


def test_new_bash_creates_documented_layout(in_tmp_path):
    result = new("bash", "my-cool-script", "-n", "acme")
    assert result.exit_code == 0

    pkg = in_tmp_path / "my-cool-script"
    assert (pkg / "meta.toml").is_file()
    assert (pkg / "LICENSE").is_file()
    assert (pkg / "README.md").is_file()
    assert (pkg / "src" / "main.sh").is_file()
    assert (pkg / "test").is_dir()


def test_manifest_is_valid_toml_with_expected_fields(in_tmp_path):
    new("bash", "my-cool-script", "-n", "acme")

    manifest = tomllib.loads((in_tmp_path / "my-cool-script" / "meta.toml").read_text())
    assert manifest["package"]["name"] == "my-cool-script"
    assert manifest["package"]["namespace"] == "acme"
    assert manifest["package"]["language"] == "bash"
    assert manifest["package"]["version"] == "0.1.0"
    assert manifest["platforms"]["os"] == ["linux", "macos"]


def test_python_package_gets_python_entrypoint(in_tmp_path):
    result = new("python", "my-tool", "--namespace", "acme")
    assert result.exit_code == 0
    assert (in_tmp_path / "my-tool" / "src" / "main.py").is_file()


def test_powershell_package_gets_ps1_entrypoint_and_windows_platform(in_tmp_path):
    result = new("powershell", "my-tool", "-n", "acme")
    assert result.exit_code == 0
    assert (in_tmp_path / "my-tool" / "src" / "main.ps1").is_file()

    manifest = tomllib.loads((in_tmp_path / "my-tool" / "meta.toml").read_text())
    assert manifest["platforms"]["os"] == ["windows"]


@pytest.mark.skipif(os.name == "nt", reason="no executable bit on Windows")
def test_posix_entrypoint_is_executable(in_tmp_path):
    new("bash", "my-cool-script", "-n", "acme")
    mode = (in_tmp_path / "my-cool-script" / "src" / "main.sh").stat().st_mode
    assert mode & 0o111


def test_namespace_is_required(in_tmp_path):
    result = new("bash", "my-cool-script")
    assert result.exit_code != 0
    assert "--namespace" in result.output
    assert not (in_tmp_path / "my-cool-script").exists()


@pytest.mark.parametrize(
    "bad_namespace",
    ["has space", "slash/ns", "Acme", "acme_org", "acme.org", "1acme", "acme-", ""],
)
def test_invalid_namespace_is_rejected(in_tmp_path, bad_namespace):
    result = new("bash", "my-cool-script", "-n", bad_namespace)
    assert result.exit_code != 0
    assert not (in_tmp_path / "my-cool-script").exists()


@pytest.mark.parametrize("good_namespace", ["acme", "acme-org2", "a", "a1-b2-c3"])
def test_valid_namespace_is_accepted(in_tmp_path, good_namespace):
    result = new("bash", "my-cool-script", "-n", good_namespace)
    assert result.exit_code == 0
    assert (in_tmp_path / "my-cool-script").is_dir()


def test_unknown_language_is_rejected(in_tmp_path):
    result = new("cobol", "my-cool-script", "-n", "acme")
    assert result.exit_code != 0
    assert "unknown language" in result.output
    assert not (in_tmp_path / "my-cool-script").exists()


@pytest.mark.parametrize("bad_name", ["Has-Caps", "under_scores", "-leading-dash", "trailing-", "spa ce"])
def test_non_kebab_case_name_is_rejected(in_tmp_path, bad_name):
    result = new("bash", bad_name, "-n", "acme")
    assert result.exit_code != 0
    if not bad_name.startswith("-"):
        # A leading-dash name is rejected earlier, as an unknown option.
        assert "kebab-case" in result.output
    assert not (in_tmp_path / bad_name).exists()


def test_existing_directory_is_refused(in_tmp_path):
    (in_tmp_path / "my-cool-script").mkdir()
    result = new("bash", "my-cool-script", "-n", "acme")
    assert result.exit_code == 1
    assert "already exists" in result.output
