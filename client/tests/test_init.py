import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

from scripticus.cli import app
from scripticus.init import on_path, path_line

runner = CliRunner()

pytestmark = pytest.mark.skipif(
    os.name == "nt", reason="profile-file PATH setup is the POSIX branch"
)


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    """Isolated HOME and SCRIPTICUS_HOME, zsh as the login shell, and a
    live PATH that does not contain the bin dir."""
    monkeypatch.setenv("HOME", str(tmp_path))
    home_dir = tmp_path / ".scripticus"
    monkeypatch.setenv("SCRIPTICUS_HOME", str(home_dir))
    monkeypatch.setenv("SHELL", "/bin/zsh")
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    return home_dir


def test_init_creates_skeleton_and_appends_profile_line(home, tmp_path):
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0, result.output

    assert (home / "bin").is_dir()
    profile = tmp_path / ".zshrc"
    assert path_line(home / "bin") in profile.read_text()
    assert "Restart your shell" in result.output


def test_init_is_idempotent(home, tmp_path):
    runner.invoke(app, ["init"])
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0, result.output

    line = path_line(home / "bin")
    assert (tmp_path / ".zshrc").read_text().count(line) == 1
    assert "already configured" in result.output


def test_profile_chosen_by_shell(home, tmp_path, monkeypatch):
    monkeypatch.setenv("SHELL", "/bin/bash")
    runner.invoke(app, ["init"])
    assert path_line(home / "bin") in (tmp_path / ".bashrc").read_text()


def test_unknown_shell_falls_back_to_profile(home, tmp_path, monkeypatch):
    monkeypatch.setenv("SHELL", "/usr/bin/fish")
    runner.invoke(app, ["init"])
    assert path_line(home / "bin") in (tmp_path / ".profile").read_text()


def test_existing_profile_content_is_preserved(home, tmp_path):
    profile = tmp_path / ".zshrc"
    profile.write_text("alias ll='ls -l'")  # note: no trailing newline

    runner.invoke(app, ["init"])

    text = profile.read_text()
    assert text.startswith("alias ll='ls -l'\n")
    assert path_line(home / "bin") in text


def test_bin_already_on_live_path_means_no_profile_edit(home, tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", f"/usr/bin:{home / 'bin'}")

    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0, result.output
    assert "already on your PATH" in result.output
    assert not (tmp_path / ".zshrc").exists()


def test_manually_added_path_entry_in_profile_is_respected(home, tmp_path):
    profile = tmp_path / ".zshrc"
    profile.write_text(f'PATH="{home / "bin"}:$PATH"\n')  # user's own wording

    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0, result.output
    assert profile.read_text().count(str(home / "bin")) == 1


def test_on_path_normalises_trailing_separators(home):
    bin_dir = home / "bin"
    assert on_path(bin_dir, environ={"PATH": f"/usr/bin:{bin_dir}{os.sep}"})
    assert not on_path(bin_dir, environ={"PATH": "/usr/bin:/bin"})
