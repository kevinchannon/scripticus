"""Package scaffolding for `scripticus new`."""

import os
from dataclasses import dataclass
from pathlib import Path

from scripticus_schema.manifest import (  # noqa: F401  (re-exported for cli validation)
    LANGUAGES,
    NAMESPACE_RE,
    PACKAGE_NAME_RE,
)


class ScaffoldError(Exception):
    """A package could not be scaffolded."""


@dataclass(frozen=True)
class ScaffoldTemplate:
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

TEMPLATES: dict[str, ScaffoldTemplate] = {
    "bash": ScaffoldTemplate(BASH_MAIN, ("linux", "macos"), executable=True),
    "python": ScaffoldTemplate(PYTHON_MAIN, ("linux", "macos", "windows"), executable=True),
    "powershell": ScaffoldTemplate(POWERSHELL_MAIN, ("windows",), executable=False),
}

MANIFEST_TEMPLATE = """\
[package]
namespace = "{namespace}"
name = "{name}"
version = "0.1.0"
language = "{language}"
# TODO: one-line description, shown in search results
description = ""

[platforms]
os = [{os_list}]
"""

# A snippet package declares neither a language nor a platform: its code is
# never run, and each variant's language is its file extension (D58). The
# scaffolded snippet is named after the package, which is the common case (one
# package, one piece of boilerplate) and shows the name -> file correspondence.
SNIPPET_MANIFEST_TEMPLATE = """\
[package]
namespace = "{namespace}"
name = "{name}"
version = "0.1.0"
# TODO: one-line description, shown in search results
description = ""

# One section per snippet, and one src/<snippet>.<ext> file per language you
# write it in — 'snip {name}.sh' prints src/{name}.sh. Add src/{name}.py
# alongside it and 'snip {name}.py' works too; the languages are read off the
# files, never listed here.
[snippet.{name}]
description = "TODO: what this boilerplate is for"
"""

SNIPPET_TEMPLATE = """\
# TODO: replace with the boilerplate to paste. It is never run — only read,
# pasted, and edited — so it can be a fragment.
"""

LICENSE_TEMPLATE = """\
TODO: add your licence text.
"""

README_TEMPLATE = """\
# {name}

TODO: describe {name}.
"""


def scaffold_snippet_package(
    name: str, namespace: str, extension: str, parent: Path
) -> list[Path]:
    """Create a new snippet package skeleton under ``parent / name`` (D58).

    Multi-file by nature — a manifest section plus one real source file per
    language — which is exactly why it is worth scaffolding: the ceremony is a
    scaffolding problem, not a format problem. No ``test/``: a snippet is never
    run, so there is nothing to run against it.
    """
    package_dir = parent / name
    if package_dir.exists():
        raise ScaffoldError(f"'{package_dir}' already exists")

    src_dir = package_dir / "src"

    created: list[Path] = []
    for directory in (package_dir, src_dir):
        directory.mkdir(parents=True)
        created.append(directory)

    files = {
        package_dir / "meta.toml": SNIPPET_MANIFEST_TEMPLATE.format(
            name=name, namespace=namespace
        ),
        package_dir / "LICENSE": LICENSE_TEMPLATE,
        package_dir / "README.md": README_TEMPLATE.format(name=name),
        src_dir / f"{name}.{extension}": SNIPPET_TEMPLATE,
    }
    for path, content in files.items():
        path.write_text(content)
        created.append(path)

    return created


def scaffold_package(language: str, name: str, namespace: str, parent: Path) -> list[Path]:
    """Create a new package skeleton under ``parent / name``.

    Returns the created paths (directories and files), in creation order.
    """
    template = TEMPLATES[language]

    package_dir = parent / name
    if package_dir.exists():
        raise ScaffoldError(f"'{package_dir}' already exists")

    src_dir = package_dir / "src"
    test_dir = package_dir / "test"
    entrypoint = src_dir / f"main.{LANGUAGES[language].extension}"

    created: list[Path] = []
    for directory in (package_dir, src_dir, test_dir):
        directory.mkdir(parents=True)
        created.append(directory)

    os_list = ", ".join(f'"{os_name}"' for os_name in template.default_os)
    files = {
        package_dir / "meta.toml": MANIFEST_TEMPLATE.format(
            name=name, namespace=namespace, language=language, os_list=os_list
        ),
        package_dir / "LICENSE": LICENSE_TEMPLATE,
        package_dir / "README.md": README_TEMPLATE.format(name=name),
        entrypoint: template.entrypoint_template.format(name=name),
    }
    for path, content in files.items():
        path.write_text(content)
        created.append(path)

    if template.executable and os.name != "nt":
        entrypoint.chmod(0o755)

    return created
