"""System-tool installation via an operator-configured command (D44).

Scripticus encodes no package-manager logic. When a resolved closure needs
system tools, the client checks each required tool for presence on ``PATH``
and, for the missing set, runs the command the operator put in
``config.toml``'s ``[tools] install`` — once, through the platform shell,
inheriting the process environment (so proxies/mirrors/credentials come from
the machine environment, never the org-distributable config). An optional
``[tools] escalate`` prefix elevates *only* that command (``sudo``/``doas``);
Scripticus itself never needs privilege.

The command runs **before any package file or shim is written** (tools-first,
D44): a tool failure aborts the install before package mutation begins. v1
"satisfiability" is PATH presence only — an install-only command cannot be
queried for available versions, so versioned tool windows are post-v1 (D43).
With no ``install`` configured, missing *required* tools abort the install
(with a ``--skip-tools`` escape); missing *optional* tools are only reported
by the caller and never installed.

Tool names are validated to a safe charset at manifest parse
(``TOOL_NAME_RE``, D44) and shell-quoted here at invocation, so a manifest
cannot inject shell.
"""

import os
import shlex
import shutil
import subprocess
from collections.abc import Iterable

from scripticus.config import Tools


class ToolError(Exception):
    """A required system tool is missing and could not be installed."""


def missing_on_path(names: Iterable[str]) -> list[str]:
    """The subset of ``names`` not found on ``PATH``, order preserved, deduped."""
    seen: set[str] = set()
    result = []
    for name in names:
        if name not in seen and shutil.which(name) is None:
            result.append(name)
        seen.add(name)
    return result


def install_command(install: str, escalate: str | None, packages: list[str]) -> str:
    """The shell command line to install ``packages`` (D44).

    The names are shell-quoted and either substituted into a ``{packages}``
    placeholder or appended when the placeholder is absent; the ``escalate``
    prefix, if any, is prepended to the whole command.
    """
    quoted = " ".join(shlex.quote(package) for package in packages)
    if "{packages}" in install:
        command = install.replace("{packages}", quoted)
    else:
        command = f"{install} {quoted}"
    if escalate:
        command = f"{escalate} {command}"
    return command


def _run_shell(command: str) -> None:
    """Run ``command`` once through the platform shell, inheriting stdio and
    the environment (so an interactive elevation prompt works). Raises
    ToolError on a non-zero exit.
    """
    argv = ["cmd", "/c", command] if os.name == "nt" else ["bash", "-lc", command]
    result = subprocess.run(argv)
    if result.returncode != 0:
        raise ToolError(
            f"tool installation command failed (exit {result.returncode}): {command}"
        )


def install_missing_required(missing_required: list[str], tools: Tools) -> None:
    """Install the missing required tools before any package mutation (D44).

    Does nothing when nothing is missing. Refuses — naming the tools — when
    required tools are missing and no ``[tools] install`` command is
    configured. Otherwise builds the command from config and runs it once.
    """
    if not missing_required:
        return
    if tools.install is None:
        listed = ", ".join(missing_required)
        raise ToolError(
            f"missing required system tools: {listed}"
            " — configure [tools] install in config.toml, install them"
            " yourself, or re-run with --skip-tools"
        )
    _run_shell(install_command(tools.install, tools.escalate, missing_required))
