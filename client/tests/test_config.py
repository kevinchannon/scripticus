from pathlib import Path

import pytest

from scripticus.config import (
    ConfigError,
    Remote,
    Tools,
    default_remote,
    find_remote,
    load_remotes,
    load_tools,
    save_remotes,
)


@pytest.fixture
def home(tmp_path) -> Path:
    return tmp_path / "scripticus-home"


def test_missing_config_file_means_no_remotes(home):
    assert load_remotes(home) == []


def test_save_and_load_round_trip_preserves_order(home):
    remotes = [
        Remote(name="origin", url="https://scripts.example.com"),
        Remote(name="public", url="https://scripticus.example.org"),
    ]
    save_remotes(home, remotes)
    assert load_remotes(home) == remotes


def test_save_creates_home_directory(home):
    save_remotes(home, [Remote(name="origin", url="https://scripts.example.com")])
    assert (home / "config.toml").is_file()


def test_saved_file_is_plain_remotes_toml(home):
    save_remotes(home, [Remote(name="origin", url="https://scripts.example.com")])
    text = (home / "config.toml").read_text()
    assert "[[remotes]]" in text
    assert 'name = "origin"' in text
    assert 'url = "https://scripts.example.com"' in text


def test_invalid_toml_is_a_config_error(home):
    home.mkdir(parents=True)
    (home / "config.toml").write_text("[[remotes\n")
    with pytest.raises(ConfigError, match="not valid TOML"):
        load_remotes(home)


def test_remote_entry_missing_url_is_a_config_error(home):
    home.mkdir(parents=True)
    (home / "config.toml").write_text('[[remotes]]\nname = "origin"\n')
    with pytest.raises(ConfigError, match="'name' and 'url'"):
        load_remotes(home)


def test_save_refuses_to_clobber_unknown_settings(home):
    home.mkdir(parents=True)
    (home / "config.toml").write_text(
        'future_setting = true\n\n[[remotes]]\nname = "origin"\nurl = "https://a"\n'
    )
    before = (home / "config.toml").read_text()
    with pytest.raises(ConfigError, match="future_setting"):
        save_remotes(home, load_remotes(home) + [Remote(name="b", url="https://b")])
    assert (home / "config.toml").read_text() == before


def test_find_remote_by_name(home):
    remotes = [Remote(name="origin", url="https://a"), Remote(name="public", url="https://b")]
    assert find_remote(remotes, "public") == Remote(name="public", url="https://b")
    assert find_remote(remotes, "nope") is None


def test_default_remote_is_first_or_none():
    assert default_remote([]) is None
    remotes = [Remote(name="origin", url="https://a"), Remote(name="public", url="https://b")]
    assert default_remote(remotes) == remotes[0]


# --- [tools] config (D44) --------------------------------------------------


def test_no_config_means_no_tools(home):
    assert load_tools(home) == Tools(install=None, escalate=None)


def test_load_tools_reads_install_and_escalate(home):
    home.mkdir(parents=True)
    (home / "config.toml").write_text(
        '[tools]\ninstall = "apt-get install -y {packages}"\nescalate = "sudo"\n'
    )
    assert load_tools(home) == Tools(
        install="apt-get install -y {packages}", escalate="sudo"
    )


def test_load_tools_escalate_optional(home):
    home.mkdir(parents=True)
    (home / "config.toml").write_text('[tools]\ninstall = "brew install {packages}"\n')
    assert load_tools(home) == Tools(install="brew install {packages}", escalate=None)


def test_load_tools_rejects_non_string_install(home):
    home.mkdir(parents=True)
    (home / "config.toml").write_text("[tools]\ninstall = 42\n")
    with pytest.raises(ConfigError, match="\\[tools\\] install must be a string"):
        load_tools(home)


def test_load_tools_rejects_non_table(home):
    home.mkdir(parents=True)
    (home / "config.toml").write_text('tools = "nope"\n')
    with pytest.raises(ConfigError, match="\\[tools\\] must be a table"):
        load_tools(home)


def test_save_remotes_preserves_tools_table(home):
    home.mkdir(parents=True)
    (home / "config.toml").write_text(
        '[[remotes]]\nname = "origin"\nurl = "https://a"\n\n'
        '[tools]\ninstall = "apt-get install -y {packages}"\nescalate = "sudo"\n'
    )
    # login registering a new remote must not drop the operator's [tools].
    save_remotes(home, load_remotes(home) + [Remote(name="b", url="https://b")])
    assert load_tools(home) == Tools(
        install="apt-get install -y {packages}", escalate="sudo"
    )
    assert [r.name for r in load_remotes(home)] == ["origin", "b"]


def test_save_remotes_still_refuses_other_unknown_settings(home):
    home.mkdir(parents=True)
    (home / "config.toml").write_text(
        'future_setting = true\n\n[tools]\ninstall = "x {packages}"\n'
    )
    before = (home / "config.toml").read_text()
    with pytest.raises(ConfigError, match="future_setting"):
        save_remotes(home, [Remote(name="b", url="https://b")])
    assert (home / "config.toml").read_text() == before
