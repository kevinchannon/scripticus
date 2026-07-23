"""Client configuration: the named, ordered remotes list (D35) and the
operator-configured tool installer (D44).

``~/.scripticus/config.toml`` holds remotes as a TOML array of tables,
``[[remotes]]``, each entry ``{ name, url }``. Array order is priority:
it is the bare-name namespace search path (D5) and `publish`'s default
target (first entry, D35) — there is no separate ``default_remote``
setting. It may also hold a ``[tools]`` table — an ``install`` command
Scripticus shells out to for system-tool installation and an ``escalate``
prefix for elevating just that command (D44). The file is managed by the
``scripticus config`` command group (``config remote`` / ``config tools``,
D56, superseding the git-pull ``config install`` of D12), and nothing
token-shaped ever lives here (that is ``credentials.toml``'s job, D34).
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


@dataclass
class Tools:
    """The ``[tools]`` table (D44): an operator-set installer command and an
    optional elevation prefix. Both absent means Scripticus never invokes a
    package manager.
    """

    install: str | None = None
    escalate: str | None = None


def _config_path(home: Path) -> Path:
    return home / "config.toml"


def _load_data(home: Path) -> dict:
    path = _config_path(home)
    if not path.is_file():
        return {}
    try:
        return tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path} is not valid TOML: {exc}") from exc


def load_remotes(home: Path) -> list[Remote]:
    """The configured remotes, in priority order. No config file yet (a
    fresh install) is an empty list, not an error.
    """
    path = _config_path(home)
    data = _load_data(home)
    remotes = []
    for entry in data.get("remotes", []):
        if not isinstance(entry, dict) or not {"name", "url"} <= entry.keys():
            raise ConfigError(
                f"{path}: each [[remotes]] entry needs both 'name' and 'url'"
            )
        remotes.append(Remote(name=entry["name"], url=entry["url"]))
    return remotes


def load_tools(home: Path) -> Tools:
    """The ``[tools]`` installer config (D44); an absent file or table means
    no installer, in which case missing required tools abort the install.
    """
    path = _config_path(home)
    table = _load_data(home).get("tools", {})
    if not isinstance(table, dict):
        raise ConfigError(f"{path}: [tools] must be a table")
    values: dict[str, str | None] = {}
    for key in ("install", "escalate"):
        value = table.get(key)
        if value is not None and not isinstance(value, str):
            raise ConfigError(f"{path}: [tools] {key} must be a string")
        values[key] = value
    return Tools(install=values["install"], escalate=values["escalate"])


def _toml_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _preserved_tools_table(home: Path) -> dict:
    """The existing ``[tools]`` table to round-trip when rewriting the file.

    Refuses rather than silently discards: any top-level setting this client
    can't round-trip (anything but ``remotes``/``tools``) is an error, as is a
    non-string ``[tools]`` value. An absent file is an empty table.
    """
    path = _config_path(home)
    if not path.is_file():
        return {}
    data = _load_data(home)
    extra = sorted(data.keys() - {"remotes", "tools"})
    if extra:
        raise ConfigError(
            f"{path} contains settings this command doesn't understand"
            f" ({', '.join(extra)}) — edit config.toml by hand"
        )
    table = data.get("tools", {})
    if not isinstance(table, dict):
        raise ConfigError(f"{path}: [tools] must be a table")
    for key, value in table.items():
        if not isinstance(value, str):
            raise ConfigError(
                f"{path}: cannot preserve [tools] {key} (not a string)"
                " — edit config.toml by hand"
            )
    return table


def _write_config(home: Path, remotes: list[Remote], tools_table: dict) -> None:
    """Rewrite ``config.toml`` from the given remotes and ``[tools]`` table —
    the sole writer, so remotes-only and tools-only edits share one format.
    """
    lines = []
    for remote in remotes:
        lines.append("[[remotes]]")
        lines.append(f"name = {_toml_string(remote.name)}")
        lines.append(f"url = {_toml_string(remote.url)}")
        lines.append("")
    if tools_table:
        lines.append("[tools]")
        for key, value in tools_table.items():
            lines.append(f"{key} = {_toml_string(value)}")
        lines.append("")
    home.mkdir(parents=True, exist_ok=True)
    _config_path(home).write_text("\n".join(lines))


def save_remotes(home: Path, remotes: list[Remote]) -> None:
    """Rewrite the remotes array, preserving list order and the operator's
    ``[tools]`` table (D44) verbatim (org-shared, and `login` must never drop
    it). Any other top-level setting this client can't round-trip makes `save`
    refuse rather than silently discard it.
    """
    _write_config(home, remotes, _preserved_tools_table(home))


def save_tools(home: Path, install: str | None, escalate: str | None) -> Tools:
    """Set the ``[tools]`` table, preserving the remotes list (D56).

    ``None`` leaves a key unchanged; the empty string clears it; any other
    value sets it. Returns the resulting :class:`Tools`.
    """
    table = _preserved_tools_table(home)
    for key, value in (("install", install), ("escalate", escalate)):
        if value is None:
            continue
        if value == "":
            table.pop(key, None)
        else:
            table[key] = value
    _write_config(home, load_remotes(home), table)
    return Tools(install=table.get("install"), escalate=table.get("escalate"))


def find_remote(remotes: list[Remote], name: str) -> Remote | None:
    for remote in remotes:
        if remote.name == name:
            return remote
    return None


def add_remote(remotes: list[Remote], name: str, url: str) -> list[Remote]:
    """Append a remote at lowest search priority (D56). A same-name, same-url
    add is idempotent (returns the list unchanged); a same-name, different-url
    add is refused, as `login` refuses to re-point a remote (D35).
    """
    existing = find_remote(remotes, name)
    if existing is not None:
        if existing.url == url:
            return remotes
        raise ConfigError(
            f"remote '{name}' already exists with a different URL"
            f" ({existing.url}) — remove it first, or use another name"
        )
    return remotes + [Remote(name=name, url=url)]


def remove_remote(remotes: list[Remote], name: str) -> list[Remote]:
    """The remotes list without ``name``; an unknown name is a ConfigError."""
    if find_remote(remotes, name) is None:
        known = ", ".join(r.name for r in remotes) or "none configured"
        raise ConfigError(f"no remote named '{name}' (remotes: {known})")
    return [remote for remote in remotes if remote.name != name]


def default_remote(remotes: list[Remote]) -> Remote | None:
    """The first configured remote — list order alone decides (D35)."""
    return remotes[0] if remotes else None
