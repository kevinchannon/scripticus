#!/usr/bin/env bats
# The publisher's retraction workflow: `yank` moving a version out of what the
# read side will serve, and `--undo` putting it back. Drives the real client
# against the live registry, because what yank changes is what a *second*
# client is then told on resolve — which one process cannot demonstrate to
# itself.
#
# `update` lives in the lifecycle arc, where it belongs: it is a step in the
# journey, not a thing on its own.

load 'lib/helpers'

setup() {
    common_setup
    run do_login
    [ "$status" -eq 0 ]
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
