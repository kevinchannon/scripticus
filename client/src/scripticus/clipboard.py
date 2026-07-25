"""The platform clipboard, behind one flag (`snip -c`, D58).

`snip x | pbcopy` copies but shows nothing, and the tool's name differs on
every platform; `-c` hides both problems by teeing — the snippet still goes to
stdout, so composition and verification are unaffected.

The degradation rule is the whole contract here: **`-c` must never cost you the
snippet**. There is no clipboard on a headless box or over plain SSH, and that
is a warning on stderr, not a failure — the caller prints the snippet either
way and this module never raises.
"""

import os
import shutil
import subprocess
import sys

# Probed in order; the first one on PATH wins. Wayland before X11 because a
# Wayland session usually still has xclip installed for XWayland, and wl-copy
# is the one that works there.
_CANDIDATES: dict[str, tuple[tuple[str, ...], ...]] = {
    "darwin": (("pbcopy",),),
    "win32": (("clip",),),
    "_posix": (
        ("wl-copy",),
        ("xclip", "-selection", "clipboard"),
        ("xsel", "--clipboard", "--input"),
    ),
}


def _commands() -> tuple[tuple[str, ...], ...]:
    if sys.platform == "darwin":
        return _CANDIDATES["darwin"]
    if os.name == "nt":
        return _CANDIDATES["win32"]
    return _CANDIDATES["_posix"]


def copy(text: str) -> str | None:
    """Put ``text`` on the clipboard; return the tool used, or None if none
    could be. Never raises — a clipboard is a convenience, and the caller has
    already committed to printing the text regardless.
    """
    for command in _commands():
        if shutil.which(command[0]) is None:
            continue
        try:
            subprocess.run(command, input=text.encode(), check=True)
        except (OSError, subprocess.CalledProcessError):
            continue  # installed but unusable (no display, say) — try the next
        return command[0]
    return None


def unavailable_reason() -> str:
    """Why `copy` returned None, phrased for the user."""
    tools = ", ".join(command[0] for command in _commands())
    return f"no clipboard tool available (looked for: {tools})"
