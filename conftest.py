import os

# Typer and Rich force colour output when they detect CI (GITHUB_ACTIONS /
# FORCE_COLOR), sprinkling ANSI codes through captured CLI output and
# breaking the tests' plain-substring assertions. Neutralise the environment
# here — conftest is imported before any test module, and therefore before
# any module-level Console is created. A dumb terminal disables all styling.
os.environ["TERM"] = "dumb"
os.environ.pop("FORCE_COLOR", None)

# Pin the console width too: Rich's non-terminal fallback is 80 columns on
# POSIX but 79 on Windows (it reserves the last column against the legacy
# console's auto-newline bug), which moves the word-wrap point and breaks
# substring assertions that straddle it. A generous fixed width keeps
# one-line messages unwrapped on every platform.
os.environ["COLUMNS"] = "120"
