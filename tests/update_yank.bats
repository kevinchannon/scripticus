#!/usr/bin/env bats
# Post-install version movement, end to end: `update` floating an installed
# package to a newer published version, and `yank`/`--undo` moving a version in
# and out of what the read side will serve. Both drive the real client against
# the live registry.

load 'lib/helpers'

setup() {
    common_setup
    run do_login
    [ "$status" -eq 0 ]
}

@test "update floats an installed package to a newer published version" {
    local ns="$SCRIPTICUS_E2E_NAMESPACE"
    local pkg
    pkg="$(unique_pkg)"

    run author_and_publish "$pkg" 0.1.0
    [ "$status" -eq 0 ]

    run scripticus install "${ns}/${pkg}" --yes
    [ "$status" -eq 0 ]
    run scripticus list --installed "${ns}/${pkg}"
    [[ "$output" == *"0.1.0"* ]]

    # A newer version lands on the remote; update re-resolves the installed
    # root against it and floats it up.
    run author_and_publish "$pkg" 0.2.0
    [ "$status" -eq 0 ]

    run scripticus update "$pkg" --yes
    [ "$status" -eq 0 ]

    run scripticus list --installed "${ns}/${pkg}"
    [ "$status" -eq 0 ]
    [[ "$output" == *"0.2.0"* ]]
}

@test "a yanked version drops out of read-side resolution, and --undo restores it" {
    local ns="$SCRIPTICUS_E2E_NAMESPACE"
    local pkg
    pkg="$(unique_pkg)"

    run author_and_publish "$pkg" 0.1.0
    [ "$status" -eq 0 ]
    run author_and_publish "$pkg" 0.2.0
    [ "$status" -eq 0 ]

    # Latest available is 0.2.0.
    run scripticus list --available "${ns}/${pkg}"
    [ "$status" -eq 0 ]
    [[ "$output" == *"0.2.0"* ]]

    # Yank it: the read side now serves 0.1.0 as latest and hides 0.2.0.
    run scripticus yank "${ns}/${pkg}@0.2.0"
    [ "$status" -eq 0 ]
    run scripticus list --available "${ns}/${pkg}"
    [ "$status" -eq 0 ]
    [[ "$output" == *"0.1.0"* ]]
    [[ "$output" != *"0.2.0"* ]]

    # --undo reverses it with no time window.
    run scripticus yank "${ns}/${pkg}@0.2.0" --undo
    [ "$status" -eq 0 ]
    run scripticus list --available "${ns}/${pkg}"
    [ "$status" -eq 0 ]
    [[ "$output" == *"0.2.0"* ]]
}
