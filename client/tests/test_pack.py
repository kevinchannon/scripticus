import os
import tarfile
import zipfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

from scripticus.cli import app
from scripticus.scaffold import scaffold_package

runner = CliRunner()


@pytest.fixture
def in_tmp_path(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


def make_package(parent: Path, language: str = "bash", name: str = "my-cool-script") -> Path:
    scaffold_package(language, name, "acme", parent)
    return parent / name


def test_pack_bash_package_produces_tarball_in_cwd(in_tmp_path):
    pkg = make_package(in_tmp_path)

    result = runner.invoke(app, ["pack", str(pkg)])
    assert result.exit_code == 0, result.output

    archive = in_tmp_path / "my_cool_script-0.1.0-linux.macos-bash.tar.gz"
    assert archive.is_file()

    with tarfile.open(archive) as tar:
        names = tar.getnames()
        assert "my-cool-script/meta.toml" in names
        assert "my-cool-script/src/main.sh" in names
        assert "my-cool-script/test" in names  # empty dir is preserved


@pytest.mark.skipif(os.name == "nt", reason="no executable bit on Windows")
def test_tarball_preserves_entrypoint_executable_bit(in_tmp_path):
    pkg = make_package(in_tmp_path)
    runner.invoke(app, ["pack", str(pkg)])

    with tarfile.open(in_tmp_path / "my_cool_script-0.1.0-linux.macos-bash.tar.gz") as tar:
        member = tar.getmember("my-cool-script/src/main.sh")
        assert member.mode & 0o111


def test_pack_windows_package_produces_zip(in_tmp_path):
    pkg = make_package(in_tmp_path, language="powershell", name="my-tool")

    result = runner.invoke(app, ["pack", str(pkg)])
    assert result.exit_code == 0, result.output

    archive = in_tmp_path / "my_tool-0.1.0-windows-powershell.zip"
    assert archive.is_file()

    with zipfile.ZipFile(archive) as zf:
        names = zf.namelist()
        assert "my-tool/meta.toml" in names
        assert "my-tool/src/main.ps1" in names
        assert "my-tool/test/" in names  # empty dir is preserved


def test_multi_platform_package_produces_one_archive_per_format(in_tmp_path):
    pkg = make_package(in_tmp_path, language="python", name="my-tool")

    result = runner.invoke(app, ["pack", str(pkg)])
    assert result.exit_code == 0, result.output

    tarball = in_tmp_path / "my_tool-0.1.0-linux.macos-python.tar.gz"
    zip_archive = in_tmp_path / "my_tool-0.1.0-windows-python.zip"
    assert tarball.is_file()
    assert zip_archive.is_file()

    # Both archives carry the same content.
    with tarfile.open(tarball) as tar:
        tar_names = {name.rstrip("/") for name in tar.getnames()}
    with zipfile.ZipFile(zip_archive) as zf:
        zip_names = {name.rstrip("/") for name in zf.namelist()}
    assert tar_names == zip_names


def test_output_option_creates_directory_and_places_archive(in_tmp_path):
    pkg = make_package(in_tmp_path)

    result = runner.invoke(app, ["pack", str(pkg), "-o", "builds"])
    assert result.exit_code == 0, result.output
    assert (in_tmp_path / "builds" / "my_cool_script-0.1.0-linux.macos-bash.tar.gz").is_file()


def test_archive_root_is_manifest_name_not_directory_name(in_tmp_path):
    pkg = make_package(in_tmp_path)
    project_dir = in_tmp_path / "my-cool-script-proj"
    pkg.rename(project_dir)

    result = runner.invoke(app, ["pack", str(project_dir)])
    assert result.exit_code == 0, result.output

    with tarfile.open(in_tmp_path / "my_cool_script-0.1.0-linux.macos-bash.tar.gz") as tar:
        assert all(name.startswith("my-cool-script/") for name in tar.getnames())


def test_prerelease_version_dashes_normalised_in_filename(in_tmp_path):
    pkg = make_package(in_tmp_path)
    manifest = pkg / "meta.toml"
    manifest.write_text(manifest.read_text().replace('"0.1.0"', '"1.0.0-alpha.1"'))

    result = runner.invoke(app, ["pack", str(pkg)])
    assert result.exit_code == 0, result.output
    assert (in_tmp_path / "my_cool_script-1.0.0_alpha.1-linux.macos-bash.tar.gz").is_file()


def test_junk_is_excluded_from_archive(in_tmp_path):
    pkg = make_package(in_tmp_path)
    (pkg / ".git").mkdir()
    (pkg / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    (pkg / "src" / "__pycache__").mkdir()
    (pkg / "src" / "__pycache__" / "main.cpython-312.pyc").write_bytes(b"\x00")
    (pkg / ".DS_Store").write_bytes(b"\x00")

    result = runner.invoke(app, ["pack", str(pkg)])
    assert result.exit_code == 0, result.output

    with tarfile.open(in_tmp_path / "my_cool_script-0.1.0-linux.macos-bash.tar.gz") as tar:
        for name in tar.getnames():
            assert ".git" not in name
            assert "__pycache__" not in name
            assert ".DS_Store" not in name


def test_missing_manifest_is_an_error(in_tmp_path):
    (in_tmp_path / "not-a-package").mkdir()

    result = runner.invoke(app, ["pack", "not-a-package"])
    assert result.exit_code == 1
    assert "no meta.toml" in result.output


def test_unset_namespace_is_an_error(in_tmp_path):
    pkg = make_package(in_tmp_path)
    manifest = pkg / "meta.toml"
    manifest.write_text(manifest.read_text().replace('namespace = "acme"', 'namespace = ""'))

    result = runner.invoke(app, ["pack", "my-cool-script"])
    assert result.exit_code == 1
    assert "namespace" in result.output


def test_non_semver_version_is_an_error(in_tmp_path):
    pkg = make_package(in_tmp_path)
    manifest = pkg / "meta.toml"
    manifest.write_text(manifest.read_text().replace('"0.1.0"', '"1.0"'))

    result = runner.invoke(app, ["pack", str(pkg)])
    assert result.exit_code == 1
    assert "semver" in result.output


def test_missing_entrypoint_is_an_error(in_tmp_path):
    pkg = make_package(in_tmp_path)
    (pkg / "src" / "main.sh").unlink()

    result = runner.invoke(app, ["pack", str(pkg)])
    assert result.exit_code == 1
    assert "src/main.sh" in result.output


def test_command_pointing_at_missing_script_is_an_error(in_tmp_path):
    pkg = make_package(in_tmp_path)
    manifest = pkg / "meta.toml"
    manifest.write_text(
        manifest.read_text() + '\n[commands]\nmy-cmd = "src/nonexistent.sh"\n'
    )

    result = runner.invoke(app, ["pack", str(pkg)])
    assert result.exit_code == 1
    assert "nonexistent.sh" in result.output
