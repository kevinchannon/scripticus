"""Which libraries a package can source, computed identically on both sides (D57).

A package's declared ``language`` does double duty: it says what interprets the
package's own scripts, and it says what that code can ``source``. Libraries are
shell-only — ``sh`` (the portable baseline) and ``bash`` (a superset of it) — so
the whole compatibility rule is one line: ``sh`` is sourceable by anything in the
family, and anything else is sourceable only by its own language.

The rule runs in two places that must never disagree (D51): the server's resolver
rejects an incompatible edge while solving a closure, and the client re-checks the
staged manifests before committing an install. Same inputs, same answer.
"""

# The languages a library may be written in (D57). Not every language
# Scripticus runs commands in: Python, PowerShell and friends have their own
# package managers, and duplicating those is a deliberate non-goal.
LIBRARY_LANGUAGES = ("sh", "bash")


def language_satisfies(consumer: str, library: str) -> bool:
    """Can a package written in ``consumer`` source a library written in ``library``?

    True iff the consumer is itself a shell and the library is either portable
    ``sh`` (every shell in the family runs it) or the consumer's own language. So
    a ``sh`` library serves both ``sh`` and ``bash`` consumers, while a ``bash``
    library serves only ``bash`` — a bashism sourced into ``sh`` is exactly the
    failure this prevents.

    D57 states the rule as ``L == "sh" or L == C``, which taken literally would
    let a ``python`` consumer "source" an ``sh`` library. The consumer-side guard
    here closes that: nothing outside the shell family sources anything at all.

    Nothing here verifies the code *is* portable ``sh``; the manifest declares
    intent and the author owns correctness (D14).
    """
    if consumer not in LIBRARY_LANGUAGES:
        return False
    return library == "sh" or library == consumer
