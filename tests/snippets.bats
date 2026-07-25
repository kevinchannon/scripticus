#!/usr/bin/env bats
# The snippet lifecycle (D58): author boilerplate in several languages, publish
# it, find it by what it contains, install it, and print it with `snip` —
# including the standalone `snip` binary, the stdout-only contract that makes
# `snip x >> script` work, and the listing that an ambiguous token gets instead
# of a silently chosen winner.

load 'lib/helpers'

setup() {
    common_setup
    run do_login
    [ "$status" -eq 0 ]
}

@test "author, publish, discover, install, and print a snippet" {
    local ns="$SCRIPTICUS_E2E_NAMESPACE"
    local pkg
    pkg="$(unique_pkg)"

    # A snippet package with one snippet in two languages: one [snippet.args]
    # section, two sibling files, no language or platform anywhere.
    run author_and_publish_snippets "$pkg" 0.1.0 \
        args.sh '# sh arg parsing' \
        args.py '# py arg parsing'
    [ "$status" -eq 0 ]
    [[ "$output" == *"Published ${pkg} 0.1.0"* ]]

    # One archive per format group, both tagged `any` (wheel-style).
    [ -f "builds/${pkg}-0.1.0-any-snippet.tar.gz" ]
    [ -f "builds/${pkg}-0.1.0-any-snippet.zip" ]

    # Discovery: the snippet's *name* is content, so searching for what you
    # want to paste finds the package that has it.
    run scripticus search args
    [ "$status" -eq 0 ]
    [[ "$output" == *"${pkg}"* ]]
    [[ "$output" == *"args.py"* ]]
    [[ "$output" == *"args.sh"* ]]

    # ...and the language labels are derived from the files, so filtering by a
    # language name reaches the extension it implies.
    run scripticus search --language python "$pkg"
    [ "$status" -eq 0 ]
    [[ "$output" == *"${pkg}"* ]]

    run scripticus install "${ns}/${pkg}" --yes
    [ "$status" -eq 0 ]

    # No shims: a snippet package takes no part in the shim system.
    run bash -c "ls '$SCRIPTICUS_HOME/bin' | wc -l"
    [ "$status" -eq 0 ]
    [[ "$(echo "$output" | tr -d '[:space:]')" == "0" ]]

    # The everyday form, through the standalone binary that makes `snip` worth
    # having: name.ext -> the snippet on stdout, and nothing else.
    run snip args.sh
    [ "$status" -eq 0 ]
    [ "$output" = "# sh arg parsing" ]

    # Same command by its other name.
    run scripticus snip args.py
    [ "$status" -eq 0 ]
    [ "$output" = "# py arg parsing" ]

    # The composition claim: stdout only, so the shell appends it for you.
    printf '#!/usr/bin/env bash\n' > script.sh
    snip args.sh >> script.sh
    run cat script.sh
    [ "$status" -eq 0 ]
    [[ "$output" == *"#!/usr/bin/env bash"* ]]
    [[ "$output" == *"# sh arg parsing"* ]]

    # A bare name with two variants lists them rather than guessing, and the
    # listing goes to stderr — a redirected stdout must never catch it.
    run bash -c "snip args > captured.txt"
    [ "$status" -eq 1 ]
    [[ "$output" == *"ambiguous"* ]]
    [[ "$output" == *"${ns}/${pkg}:args.sh"* ]]
    [ ! -s captured.txt ]

    # The fully namespaced form is how you resolve it.
    run snip "${ns}/${pkg}:args.sh"
    [ "$status" -eq 0 ]
    [ "$output" = "# sh arg parsing" ]
}

@test "two packages providing the same snippet token list instead of last-install-wins" {
    local ns="$SCRIPTICUS_E2E_NAMESPACE"
    local first second
    first="$(unique_pkg)"
    second="$(unique_pkg)"

    run author_and_publish_snippets "$first" 0.1.0 trap.sh '# first trap'
    [ "$status" -eq 0 ]
    run author_and_publish_snippets "$second" 0.1.0 trap.sh '# second trap'
    [ "$status" -eq 0 ]

    run scripticus install "${ns}/${first}" --yes
    [ "$status" -eq 0 ]
    run scripticus install "${ns}/${second}" --yes
    [ "$status" -eq 0 ]

    # The second install does not take the token: with no shim to own, there is
    # nothing to win, so `snip` shows both and lets the user choose.
    run snip trap.sh
    [ "$status" -eq 1 ]
    [[ "$output" == *"${ns}/${first}:trap.sh"* ]]
    [[ "$output" == *"${ns}/${second}:trap.sh"* ]]

    run snip "${ns}/${first}:trap.sh"
    [ "$status" -eq 0 ]
    [ "$output" = "# first trap" ]
}
