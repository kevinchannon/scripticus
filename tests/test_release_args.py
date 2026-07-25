"""Argument handling of scripts/release, the automation behind `tt release`.

Only the argument surface is covered here: everything past it talks to git,
GitHub and PyPI, and is exercised for real by `tt release <pkg>=<bump>
dry_run=yes`. Validation deliberately runs *before* any of that, so these tests
need no network, no auth and no clean tree — which is also why a typo'd bump
fails instantly rather than halfway through a release.

Every run here is fenced behind a `gh` that reports itself unauthenticated, so
a run that gets past the parser dies in the preflight. Nothing in this file may
reach the real thing: `dry_run=no` on a clean tree with a real gh would tag and
push for real.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "release"


@pytest.fixture(scope="module")
def fenced_path(tmp_path_factory: pytest.TempPathFactory) -> str:
    """A PATH whose `gh` always fails `gh auth status`."""
    bin_dir = tmp_path_factory.mktemp("bin")
    gh = bin_dir / "gh"
    gh.write_text("#!/bin/sh\nexit 1\n")
    gh.chmod(0o755)
    return f"{bin_dir}:{os.environ['PATH']}"


@pytest.fixture
def run(fenced_path: str):
    def _run(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [str(SCRIPT), *args],
            env={**os.environ, "PATH": fenced_path},
            capture_output=True,
            text=True,
            check=False,
        )

    return _run


def test_no_arguments_explains_the_syntax_and_fails(run) -> None:
    result = run()

    assert result.returncode == 1
    assert "Nothing to release" in result.stderr
    assert "none | patch | minor | major" in result.stderr


def test_every_package_defaulting_to_none_is_the_same_as_no_arguments(run) -> None:
    result = run("common=none", "schema=none", "server=none", "client=none")

    assert result.returncode == 1
    assert "Nothing to release" in result.stderr


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (("client=huge",), "bad bump 'huge'"),
        (("clint=patch",), "unknown argument 'clint'"),
        (("patch",), "expected key=value"),
        (("client=patch", "dry_run=maybe"), "bad dry_run 'maybe'"),
    ],
)
def test_bad_arguments_are_rejected_before_anything_happens(
    run, args: tuple[str, ...], expected: str
) -> None:
    result = run(*args)

    assert result.returncode == 2
    assert expected in result.stderr
    assert "usage:" in result.stderr
    # Nothing reached the release itself — no plan, no preflight complaints.
    assert "Release plan" not in result.stdout
    assert "not authenticated" not in result.stdout


@pytest.mark.parametrize("value", ["yes", "no"])
def test_valid_arguments_get_through_to_the_preflight(run, value: str) -> None:
    """Both dry_run forms parse, and a release genuinely starts: the run then
    stops on the fenced `gh`, not on a usage error."""
    result = run("client=patch", f"dry_run={value}")

    assert "not authenticated" in result.stdout + result.stderr
    assert "usage:" not in result.stderr
