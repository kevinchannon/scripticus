"""Package scaffolding for `scripticus new`."""

import os
import re
from dataclasses import dataclass
from pathlib import Path

# Package names are kebab-case (enforced again at publish; validated here so
# authors find out before they have written any code).
PACKAGE_NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


class ScaffoldError(Exception):
    """A package could not be scaffolded."""


@dataclass(frozen=True)
class Language:
    extension: str
    entrypoint_template: str
    default_os: tuple[str, ...]
    executable: bool


BASH_MAIN = """\
#!/usr/bin/env bash
set -euo pipefail

echo "Hello from {name}!"
"""

PYTHON_MAIN = """\
#!/usr/bin/env python3

print("Hello from {name}!")
"""

POWERSHELL_MAIN = """\
Write-Output "Hello from {name}!"
"""

LANGUAGES: dict[str, Language] = {
    "bash": Language("sh", BASH_MAIN, ("linux", "macos"), executable=True),
    "python": Language("py", PYTHON_MAIN, ("linux", "macos", "windows"), executable=True),
    "powershell": Language("ps1", POWERSHELL_MAIN, ("windows",), executable=False),
}

MANIFEST_TEMPLATE = """\
[package]
# TODO: set to your publishing namespace (a Gitea user or organisation)
namespace = ""
name = "{name}"
version = "0.1.0"
language = "{language}"
# TODO: one-line description, shown in search results
description = ""

[platforms]
os = [{os_list}]
"""

LICENSE_TEMPLATE = """\
TODO: add your licence text.
"""

README_TEMPLATE = """\
# {name}

TODO: describe {name}.
"""


def scaffold_package(language: str, name: str, parent: Path) -> list[Path]:
    """Create a new package skeleton under ``parent / name``.

    Returns the created paths (directories and files), in creation order.
    """
    lang = LANGUAGES[language]

    package_dir = parent / name
    if package_dir.exists():
        raise ScaffoldError(f"'{package_dir}' already exists")

    src_dir = package_dir / "src"
    test_dir = package_dir / "test"
    entrypoint = src_dir / f"main.{lang.extension}"

    created: list[Path] = []
    for directory in (package_dir, src_dir, test_dir):
        directory.mkdir(parents=True)
        created.append(directory)

    os_list = ", ".join(f'"{os_name}"' for os_name in lang.default_os)
    files = {
        package_dir / "meta.toml": MANIFEST_TEMPLATE.format(
            name=name, language=language, os_list=os_list
        ),
        package_dir / "LICENSE": LICENSE_TEMPLATE,
        package_dir / "README.md": README_TEMPLATE.format(name=name),
        entrypoint: lang.entrypoint_template.format(name=name),
    }
    for path, content in files.items():
        path.write_text(content)
        created.append(path)

    if lang.executable and os.name != "nt":
        entrypoint.chmod(0o755)

    return created
