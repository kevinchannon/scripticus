#!/usr/bin/env bats
# The library lifecycle (D57): publish reusable shell code, depend on it from a
# command, and have that command source it at run time through `scr_load` —
# which is the only place the whole path (resolve → install → stage → source)
# is exercised for real, plus the D57 claim that upgrading a library needs no
# change to its consumers.
#
# The compatibility rule (which languages may source which) and the orphaned-
# library advisory are pure logic: common/tests/test_language_compat.py,
# client/tests/test_library.py, and client/tests/test_uninstall.py.

load 'lib/helpers'

setup() {
    common_setup
    run do_login
    [ "$status" -eq 0 ]
}

@test "a command sources a published library through scr_load" {
    local ns="$SCRIPTICUS_E2E_NAMESPACE"
    local lib app
    lib="$(unique_pkg)"
    app="$(unique_pkg)"

    run author_and_publish_library "$lib" 0.1.0 sh scr_greet "hello from the library"
    [ "$status" -eq 0 ]
    [[ "$output" == *"Published ${lib} 0.1.0"* ]]

    # A library is discoverable by identity and description, and says what kind
    # of thing it is — it offers nothing runnable to list. (Matched loosely:
    # the row is a Rich table that wraps at the terminal width, so the tag and
    # the identity are not reliably on one line.)
    run scripticus search "$lib"
    [ "$status" -eq 0 ]
    [[ "$output" == *"${lib}"* ]]
    [[ "$output" == *"library:"* ]]
    [[ "$output" == *"scr_load"* ]]

    run author_and_publish_consumer "$app" 0.1.0 "$lib" '^0.1' \
        "scr_load ${ns}/${lib}
scr_greet"
    [ "$status" -eq 0 ]

    # Installing the consumer pulls the library in as a dependency.
    run scripticus install "${ns}/${app}" --yes
    [ "$status" -eq 0 ]
    [[ "$output" == *"${lib}"* ]]

    # The library is staged at a version-less path and takes no shim.
    [ -f "$SCRIPTICUS_HOME/lib/${ns}/${lib}/load.sh" ]
    [ -f "$SCRIPTICUS_HOME/lib/scr_load.sh" ]
    [ ! -e "$SCRIPTICUS_HOME/bin/${ns}.${lib}.${lib}" ]

    # The payoff: the command finds scr_load in scope with no arrangement of
    # its own, and the library's function works.
    run "$app"
    [ "$status" -eq 0 ]
    [ "$output" = "hello from the library" ]
}

@test "an updated library is picked up without touching the consumer" {
    local ns="$SCRIPTICUS_E2E_NAMESPACE"
    local lib app
    lib="$(unique_pkg)"
    app="$(unique_pkg)"

    run author_and_publish_library "$lib" 0.1.0 sh scr_greet "first version"
    [ "$status" -eq 0 ]
    run author_and_publish_consumer "$app" 0.1.0 "$lib" '^0.1' \
        "scr_load ${ns}/${lib}
scr_greet"
    [ "$status" -eq 0 ]
    run scripticus install "${ns}/${app}" --yes
    [ "$status" -eq 0 ]
    run "$app"
    [ "$output" = "first version" ]

    run author_and_publish_library "$lib" 0.1.1 sh scr_greet "second version"
    [ "$status" -eq 0 ]

    run scripticus update "${ns}/${lib}" --yes
    [ "$status" -eq 0 ]

    # The version-less staging path is what makes this work: the consumer's
    # script is unchanged and unrebuilt, and picks up the new code.
    run "$app"
    [ "$status" -eq 0 ]
    [ "$output" = "second version" ]
}

