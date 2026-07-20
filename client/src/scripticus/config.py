"""Client configuration: the named, ordered remotes list (D35).

``~/.scripticus/config.toml`` holds remotes as a TOML array of tables,
``[[remotes]]``, each entry ``{ name, url }``. Array order is priority:
it is the bare-name namespace search path (D5) and `publish`'s default
target (first entry, D35) — there is no separate ``default_remote``
setting. The file is org-distributable via ``scripticus config install``
(D12), which is why nothing token-shaped ever lives here (that is
``credentials.toml``'s job, D34).
"""

import tomllib
from dataclasses import dataclass
from pathlib import Path


class ConfigError(Exception):
    """The client configuration cannot be read or written."""


@dataclass
class Remote:
    name: str
    url: str


def _config_path(home: Path) -> Path:
    return home / "config.toml"


def load_remotes(home: Path) -> list[Remote]:
    """The configured remotes, in priority order. No config file yet (a
    fresh install) is an empty list, not an error.
    """
    path = _config_path(home)
    if not path.is_file():
        return []
    try:
        data = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path} is not valid TOML: {exc}") from exc

    remotes = []
    for entry in data.get("remotes", []):
        if not isinstance(entry, dict) or not {"name", "url"} <= entry.keys():
            raise ConfigError(
                f"{path}: each [[remotes]] entry needs both 'name' and 'url'"
            )
        remotes.append(Remote(name=entry["name"], url=entry["url"]))
    return remotes


def _toml_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def save_remotes(home: Path, remotes: list[Remote]) -> None:
    """Rewrite ``config.toml``'s remotes array, preserving list order.

    The remotes array is the only setting `login` may touch. If the file
    carries anything else (hand-added future settings this client version
    doesn't know how to round-trip), refuse rather than silently dropping
    it — the user can add the remote by hand.
    """
    path = _config_path(home)
    if path.is_file():
        data = tomllib.loads(path.read_text())
        extra = sorted(data.keys() - {"remotes"})
        if extra:
            raise ConfigError(
                f"{path} contains settings this command doesn't understand"
                f" ({', '.join(extra)}) — add the [[remotes]] entry manually"
            )

    lines = []
    for remote in remotes:
        lines.append("[[remotes]]")
        lines.append(f"name = {_toml_string(remote.name)}")
        lines.append(f"url = {_toml_string(remote.url)}")
        lines.append("")
    home.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines))


def find_remote(remotes: list[Remote], name: str) -> Remote | None:
    for remote in remotes:
        if remote.name == name:
            return remote
    return None


def default_remote(remotes: list[Remote]) -> Remote | None:
    """The first configured remote — list order alone decides (D35)."""
    return remotes[0] if remotes else None
