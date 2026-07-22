#!/usr/bin/env bats
# The end-to-end happy path from the README: author a package, publish it to a
# real registry, discover it two ways, install it, and run the installed
# command. This single test exercises new -> pack -> login -> publish ->
# list/search -> install -> the shim on PATH, all against the live stack.

load 'lib/helpers'

setup() {
    common_setup
}

@test "author, publish, discover, install, and run a package" {
    local ns="$SCRIPTICUS_E2E_NAMESPACE"
    local pkg
    pkg="$(unique_pkg)"

    # login registers the 'origin' remote and stores the verified token in the
    # credential store (D34/D41); the publish below then authenticates from it.
    run do_login
    [ "$status" -eq 0 ]
    [[ "$output" == *"Logged in to origin"* ]]

    run scripticus new bash "$pkg" -n "$ns"
    [ "$status" -eq 0 ]
    [ -f "$pkg/meta.toml" ]

    run scripticus pack "$pkg" -o builds
    [ "$status" -eq 0 ]
    run bash -c "ls builds/${pkg}-0.1.0-*.tar.gz"
    [ "$status" -eq 0 ]

    # No SCRIPTICUS_TOKEN here: this publish authenticates from the credential
    # store that login just populated.
    run scripticus publish "builds/${pkg}-0.1.0"
    [ "$status" -eq 0 ]
    [[ "$output" == *"Published ${pkg} 0.1.0"* ]]

    # Discovery: `list` by identity and `search` by content both find it.
    run scripticus list "${ns}/${pkg}"
    [ "$status" -eq 0 ]
    [[ "$output" == *"${ns}/${pkg}"* ]]
    [[ "$output" == *"0.1.0"* ]]

    run scripticus search "$pkg"
    [ "$status" -eq 0 ]
    [[ "$output" == *"${pkg}"* ]]

    # Install (resolve -> stage -> apply), then run the installed shim by name;
    # $SCRIPTICUS_HOME/bin is on PATH (what `init` bootstraps for a user).
    run scripticus install "${ns}/${pkg}" --yes
    [ "$status" -eq 0 ]

    run "$pkg"
    [ "$status" -eq 0 ]
    [[ "$output" == *"Hello from ${pkg}!"* ]]

    # And it now appears in the offline Installed listing.
    run scripticus list --installed "${ns}/${pkg}"
    [ "$status" -eq 0 ]
    [[ "$output" == *"${ns}/${pkg}"* ]]
}
