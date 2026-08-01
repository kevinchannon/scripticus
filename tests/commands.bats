#!/usr/bin/env bats
# Command-shim claims from the README that the lifecycle test doesn't reach:
# a multi-command package exposing a shim per command (and the guaranteed
# fully-qualified form), and `uninstall` removing a package's shims. Each just
# checks the README isn't lying.
#
# Shim arbitration itself — `use` re-pointing a contested convenience shim,
# collisions, ownership — is client/tests/test_use.py and test_install.py,
# which cover far more of it than a live stack usefully can.

load 'lib/helpers'

setup() {
    common_setup
    run do_login
    [ "$status" -eq 0 ]
}

@test "a multi-command package installs a shim per command, plus the fully-qualified form" {
    local ns="$SCRIPTICUS_E2E_NAMESPACE"
    local pkg
    pkg="$(unique_pkg)"

    run author_and_publish_cmds "$pkg" 0.1.0 alpha "alpha-ran" beta "beta-ran"
    [ "$status" -eq 0 ]

    run scripticus install "${ns}/${pkg}" --yes
    [ "$status" -eq 0 ]

    # Both commands are runnable by their bare names.
    run alpha
    [ "$status" -eq 0 ]
    [[ "$output" == *"alpha-ran"* ]]
    run beta
    [ "$status" -eq 0 ]
    [[ "$output" == *"beta-ran"* ]]

    # The <ns>.<pkg>.<cmd> form is guaranteed unique and always present.
    run "${ns}.${pkg}.alpha"
    [ "$status" -eq 0 ]
    [[ "$output" == *"alpha-ran"* ]]
}

@test "uninstall removes a package's command shims" {
    local ns="$SCRIPTICUS_E2E_NAMESPACE"
    local pkg
    pkg="$(unique_pkg)"

    run author_and_publish_cmds "$pkg" 0.1.0 solo "solo-ran"
    [ "$status" -eq 0 ]
    run scripticus install "${ns}/${pkg}" --yes
    [ "$status" -eq 0 ]
    run solo
    [ "$status" -eq 0 ]

    run scripticus uninstall "${ns}/${pkg}" -y
    [ "$status" -eq 0 ]

    # The shim is gone: the bare command no longer resolves, and the package is
    # no longer in the installed listing.
    run bash -c "command -v solo"
    [ "$status" -ne 0 ]
    run scripticus list --installed "${ns}/${pkg}"
    [ "$status" -eq 0 ]
    [[ "$output" != *"${ns}/${pkg}"* ]]
}

