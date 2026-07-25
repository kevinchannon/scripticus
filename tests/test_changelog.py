"""Unit tests for scripts/changelog, the per-package CHANGELOG.md surgery
`tt release` drives (D59).

These are plain pytest (collected by the root `uv run pytest` / `tt unit-test`)
rather than BATS: they need no stack, no client, and no container — just the
script and a temporary changelog. The rest of tests/ is the full-stack BATS
suite, which is a different kind of thing entirely.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "changelog"

TEMPLATE = """# Changelog

Preamble that must survive untouched.

## Unreleased

### common

### schema

- Something schema-shaped.

### server

- A server line.
- A second server line.

### client

## client 0.6.0 — 2026-07-19

- The previous release.
"""


def run(args, changelog: Path, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(SCRIPT), *args],
        cwd=cwd,
        env={"CHANGELOG_FILE": str(changelog), "PATH": "/usr/bin:/bin:/usr/sbin"},
        capture_output=True,
        text=True,
        check=True,
    )


@pytest.fixture
def changelog(tmp_path: Path) -> Path:
    path = tmp_path / "CHANGELOG.md"
    path.write_text(TEMPLATE)
    return path


def test_section_returns_only_that_packages_bullets(changelog: Path) -> None:
    out = run(["section", "server"], changelog).stdout
    assert out == "- A server line.\n- A second server line.\n"


def test_section_is_empty_for_a_package_with_no_entries(changelog: Path) -> None:
    assert run(["section", "client"], changelog).stdout == ""


def test_section_ignores_already_released_sections(changelog: Path) -> None:
    """The 0.6.0 bullet lives under a released heading, not under Unreleased."""
    assert "previous release" not in run(["section", "client"], changelog).stdout


def test_section_is_silent_when_the_file_is_missing(tmp_path: Path) -> None:
    result = run(["section", "client"], tmp_path / "nope.md")
    assert result.stdout == ""


def test_release_moves_the_block_into_a_dated_section(changelog: Path) -> None:
    run(["release", "server", "0.7.0", "2026-07-25"], changelog)
    text = changelog.read_text()

    assert "## server 0.7.0 — 2026-07-25\n\n- A server line.\n- A second server line.\n" in text
    # ...and the released section sits above the previous release, newest-first.
    assert text.index("## server 0.7.0") < text.index("## client 0.6.0")


def test_release_empties_the_unreleased_block_but_keeps_the_heading(
    changelog: Path,
) -> None:
    run(["release", "server", "0.7.0", "2026-07-25"], changelog)
    assert run(["section", "server"], changelog).stdout == ""
    assert "### server" in changelog.read_text()


def test_release_leaves_other_packages_pending_entries_alone(changelog: Path) -> None:
    run(["release", "server", "0.7.0", "2026-07-25"], changelog)
    assert run(["section", "schema"], changelog).stdout == "- Something schema-shaped.\n"


def test_release_preserves_the_preamble_and_unreleased_scaffold(
    changelog: Path,
) -> None:
    run(["release", "server", "0.7.0", "2026-07-25"], changelog)
    text = changelog.read_text()

    assert "Preamble that must survive untouched." in text
    assert "## Unreleased" in text
    for package in ("common", "schema", "server", "client"):
        assert f"### {package}" in text


def test_release_is_a_no_op_when_nothing_is_pending(changelog: Path) -> None:
    before = changelog.read_text()
    run(["release", "client", "0.6.1", "2026-07-25"], changelog)
    assert changelog.read_text() == before


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A throwaway git repo with one commit, for the tag-notes cases."""
    work = tmp_path / "repo"
    work.mkdir()
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", str(work)]
    subprocess.run([*git, "init", "-q"], check=True)
    (work / "f").write_text("x\n")
    subprocess.run([*git, "add", "f"], check=True)
    subprocess.run(
        [*git, "commit", "-q", "-m", "Subject of the commit", "-m", "Commit body."],
        check=True,
    )
    return work


def tag(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", str(repo), "tag", *args],
        check=True,
    )


def test_tag_notes_returns_the_annotation_body(repo: Path, changelog: Path) -> None:
    tag(repo, "-a", "client-v0.6.1", "-m", "scripticus 0.6.1", "-m", "- A change.\n- Another.")
    out = run(["tag-notes", "client-v0.6.1"], changelog, cwd=repo).stdout

    assert out == "- A change.\n- Another.\n"


def test_tag_notes_is_empty_for_a_subject_only_annotated_tag(
    repo: Path, changelog: Path
) -> None:
    """Tags cut before D59 carry just '<dist> <version>'."""
    tag(repo, "-a", "client-v0.6.0", "-m", "scripticus 0.6.0")
    assert run(["tag-notes", "client-v0.6.0"], changelog, cwd=repo).stdout == ""


def test_tag_notes_never_falls_through_to_the_commit_message(
    repo: Path, changelog: Path
) -> None:
    """`git tag --format` on a lightweight tag reports the commit's message —
    which must not end up as release notes."""
    tag(repo, "v0.1.0")
    out = run(["tag-notes", "v0.1.0"], changelog, cwd=repo).stdout

    assert out == ""
    assert "Commit body." not in out


def test_tag_notes_is_empty_for_an_unknown_tag(repo: Path, changelog: Path) -> None:
    assert run(["tag-notes", "client-v9.9.9"], changelog, cwd=repo).stdout == ""


def test_a_released_section_survives_the_round_trip_to_a_tag(
    repo: Path, changelog: Path
) -> None:
    """The whole D59 loop: pending block -> tag annotation -> release notes."""
    notes = run(["section", "server"], changelog).stdout.rstrip("\n")
    run(["release", "server", "0.7.0", "2026-07-25"], changelog)
    tag(repo, "-a", "server-v0.7.0", "-m", "scripticus-server 0.7.0", "-m", notes)

    assert run(["tag-notes", "server-v0.7.0"], changelog, cwd=repo).stdout == notes + "\n"


def test_releasing_several_packages_stacks_newest_first(changelog: Path) -> None:
    """`tt release` rolls in reverse tier order (client first, common last), so
    the file ends up reading common, schema, server, client from the top."""
    run(["release", "server", "0.7.0", "2026-07-25"], changelog)
    run(["release", "schema", "0.5.0", "2026-07-25"], changelog)
    text = changelog.read_text()

    assert text.index("## schema 0.5.0") < text.index("## server 0.7.0")
    assert text.index("## server 0.7.0") < text.index("## client 0.6.0")
