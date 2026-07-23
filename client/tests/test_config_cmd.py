from pathlib import Path

import pytest
from typer.testing import CliRunner

from scripticus.cli import app
from scripticus.config import Remote, Tools, load_remotes, load_tools, save_remotes

runner = CliRunner()


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    home_dir = tmp_path / "scripticus-home"
    monkeypatch.setenv("SCRIPTICUS_HOME", str(home_dir))
    return home_dir


# --- config remote add -----------------------------------------------------


def test_remote_add_registers_first_remote(home):
    result = runner.invoke(
        app, ["config", "remote", "add", "origin", "https://a.example.com"]
    )
    assert result.exit_code == 0, result.output
    assert "Added remote" in result.output
    assert load_remotes(home) == [Remote(name="origin", url="https://a.example.com")]


def test_remote_add_appends_at_lowest_priority(home):
    save_remotes(home, [Remote(name="origin", url="https://a.example.com")])

    result = runner.invoke(
        app, ["config", "remote", "add", "public", "https://b.example.com"]
    )
    assert result.exit_code == 0, result.output
    assert load_remotes(home) == [
        Remote(name="origin", url="https://a.example.com"),
        Remote(name="public", url="https://b.example.com"),
    ]


def test_remote_add_same_name_same_url_is_a_noop(home):
    save_remotes(home, [Remote(name="origin", url="https://a.example.com")])
    before = (home / "config.toml").read_text()

    result = runner.invoke(
        app, ["config", "remote", "add", "origin", "https://a.example.com"]
    )
    assert result.exit_code == 0, result.output
    assert "already configured" in result.output
    assert (home / "config.toml").read_text() == before


def test_remote_add_conflicting_url_is_refused(home):
    save_remotes(home, [Remote(name="origin", url="https://a.example.com")])

    result = runner.invoke(
        app, ["config", "remote", "add", "origin", "https://elsewhere.example.com"]
    )
    assert result.exit_code == 1
    assert "already exists with a different URL" in result.output
    assert load_remotes(home) == [Remote(name="origin", url="https://a.example.com")]


def test_remote_add_preserves_the_tools_table(home):
    save_remotes(home, [Remote(name="origin", url="https://a.example.com")])
    (home / "config.toml").write_text(
        (home / "config.toml").read_text()
        + '\n[tools]\ninstall = "apt-get install -y {packages}"\n'
    )

    result = runner.invoke(
        app, ["config", "remote", "add", "public", "https://b.example.com"]
    )
    assert result.exit_code == 0, result.output
    assert load_tools(home) == Tools(install="apt-get install -y {packages}", escalate=None)


# --- config remote list ----------------------------------------------------


def test_remote_list_shows_remotes_in_priority_order(home):
    save_remotes(
        home,
        [
            Remote(name="origin", url="https://a.example.com"),
            Remote(name="public", url="https://b.example.com"),
        ],
    )
    result = runner.invoke(app, ["config", "remote", "list"])
    assert result.exit_code == 0, result.output
    assert "origin" in result.output and "public" in result.output
    # Priority order: origin (1) is listed above public (2).
    assert result.output.index("origin") < result.output.index("public")


def test_remote_list_with_no_remotes(home):
    result = runner.invoke(app, ["config", "remote", "list"])
    assert result.exit_code == 0, result.output
    assert "No remotes configured" in result.output


# --- config remote remove --------------------------------------------------


def test_remote_remove_drops_the_remote(home):
    save_remotes(
        home,
        [
            Remote(name="origin", url="https://a.example.com"),
            Remote(name="public", url="https://b.example.com"),
        ],
    )
    result = runner.invoke(app, ["config", "remote", "remove", "origin"])
    assert result.exit_code == 0, result.output
    assert load_remotes(home) == [Remote(name="public", url="https://b.example.com")]


def test_remote_remove_unknown_is_refused(home):
    save_remotes(home, [Remote(name="origin", url="https://a.example.com")])
    result = runner.invoke(app, ["config", "remote", "remove", "nope"])
    assert result.exit_code == 1
    assert "no remote named 'nope'" in result.output
    assert load_remotes(home) == [Remote(name="origin", url="https://a.example.com")]


# --- config tools ----------------------------------------------------------


def test_tools_set_install_and_escalate(home):
    result = runner.invoke(
        app,
        [
            "config",
            "tools",
            "--install",
            "dnf install -y {packages}",
            "--escalate",
            "sudo",
        ],
    )
    assert result.exit_code == 0, result.output
    assert load_tools(home) == Tools(install="dnf install -y {packages}", escalate="sudo")


def test_tools_show_reports_current_values(home):
    runner.invoke(app, ["config", "tools", "--install", "brew install {packages}"])
    result = runner.invoke(app, ["config", "tools"])
    assert result.exit_code == 0, result.output
    assert "brew install {packages}" in result.output
    assert "(not set)" in result.output  # escalate is unset


def test_tools_empty_string_clears_a_key(home):
    runner.invoke(
        app,
        ["config", "tools", "--install", "brew install {packages}", "--escalate", "sudo"],
    )
    result = runner.invoke(app, ["config", "tools", "--install", ""])
    assert result.exit_code == 0, result.output
    assert load_tools(home) == Tools(install=None, escalate="sudo")


def test_tools_set_preserves_remotes(home):
    save_remotes(home, [Remote(name="origin", url="https://a.example.com")])
    runner.invoke(app, ["config", "tools", "--install", "brew install {packages}"])
    assert load_remotes(home) == [Remote(name="origin", url="https://a.example.com")]
