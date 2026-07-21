"""Deciding what `scripticus login` does before any prompt or write (D35).

`login <name>` authenticates to an already-configured remote; the
two-argument form `login <name> <url>` doubles as first-time remote
registration, appending the remote to ``config.toml`` (lowest search
priority). A URL that conflicts with an existing remote's is refused
outright — a command named for authentication never silently re-points
a remote.
"""

from scripticus.config import Remote, find_remote


class LoginError(Exception):
    """The login command cannot proceed."""


def prepare_login(
    name: str, url: str | None, remotes: list[Remote]
) -> tuple[Remote, list[Remote] | None]:
    """The remote to store a token for, plus the updated remotes list when
    this login also registers a new remote (``None`` when config is
    untouched).
    """
    existing = find_remote(remotes, name)

    if url is None:
        if existing is None:
            known = ", ".join(r.name for r in remotes) or "none configured"
            raise LoginError(
                f"no remote named '{name}' (remotes: {known})"
                f" — to register it, give its URL: scripticus login {name} <url>"
            )
        return existing, None

    if existing is None:
        added = Remote(name=name, url=url)
        return added, remotes + [added]
    if existing.url != url:
        raise LoginError(
            f"remote '{name}' is already configured with a different URL"
            f" ({existing.url}) — logins never re-point a remote"
        )
    # Same URL: redundant confirmation, proceed as the one-argument form.
    return existing, None
