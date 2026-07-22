#!/usr/bin/env bats
# Command-shim claims from the README that the lifecycle test doesn't reach:
# a multi-command package exposing a shim per command (and the guaranteed
# fully-qualified form), `uninstall` removing a package's shims, and `use`
# re-pointing a contested convenience shim. Each just checks the README isn't
# lying; the mechanics are covered in depth by the pytest suite.

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

@test "use re-points a contested convenience shim at a chosen package" {
    local ns="$SCRIPTICUS_E2E_NAMESPACE"
    local a b
    a="$(unique_pkg)a"
    b="$(unique_pkg)b"

    # Two packages providing the same command name — the convenience shim
    # collides on purpose.
    run author_and_publish_cmds "$a" 0.1.0 contested "from-${a}"
    [ "$status" -eq 0 ]
    run author_and_publish_cmds "$b" 0.1.0 contested "from-${b}"
    [ "$status" -eq 0 ]

    run scripticus install "${ns}/${a}" --yes
    [ "$status" -eq 0 ]
    # The second install takes the contested bare shim (last-install-wins);
    # --force=all accepts the overwrite non-interactively.
    run scripticus install "${ns}/${b}" --force=all
    [ "$status" -eq 0 ]

    run contested
    [ "$status" -eq 0 ]
    [[ "$output" == *"from-${b}"* ]]

    # Re-point the bare shim back at the first package.
    run scripticus use "${ns}/${a}" contested
    [ "$status" -eq 0 ]

    run contested
    [ "$status" -eq 0 ]
    [[ "$output" == *"from-${a}"* ]]
}
