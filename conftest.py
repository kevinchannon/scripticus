import os

# Typer and Rich force colour output when they detect CI (GITHUB_ACTIONS /
# FORCE_COLOR), sprinkling ANSI codes through captured CLI output and
# breaking the tests' plain-substring assertions. Neutralise the environment
# here — conftest is imported before any test module, and therefore before
# any module-level Console is created. A dumb terminal disables all styling.
os.environ["TERM"] = "dumb"
os.environ.pop("FORCE_COLOR", None)
