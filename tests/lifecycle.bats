#!/usr/bin/env bats
# The whole user journey, in order, on a machine that starts with nothing:
# log in, author, pack, publish, discover, install, update, uninstall.
#
# Deliberately a connected series rather than one big test: every step is a
# command a user actually types, and naming them separately means a failure
# says *which* step broke instead of pointing at line 70 of a monolith. They
# share one SCRIPTICUS_HOME and one package for the whole file (setup_file),
# because that is what makes it a journey — each step consumes what the last
# one produced.
#
# The machine really is bare. Unlike the rest of the suite this file does not
# pre-export the bin directory onto PATH; it starts with no `~/.scripticus` and
# no profile, exactly as the client README describes (`pipx install` then
# `login` — there is no separate init step since D63). What puts the bin
# directory on PATH is the first `install`, and sourcing the profile it edited
# is this suite's stand-in for the shell restart it tells you to do.

setup_file() {
    : "${SCRIPTICUS_E2E_URL:?should be exported by tests/e2e.sh}"
    : "${SCRIPTICUS_E2E_TOKEN:?should be exported by tests/e2e.sh}"
    : "${SCRIPTICUS_E2E_NAMESPACE:?should be exported by tests/e2e.sh}"

    ARC_ROOT="$(mktemp -d)"
    export ARC_ROOT
    export ARC_SCRIPTICUS_HOME="$ARC_ROOT/scripticus"   # the client's state
    export ARC_USER_HOME="$ARC_ROOT/user"               # $HOME: where the bootstrap writes
    export ARC_WORK="$ARC_ROOT/work"                    # the author's working directory
    export ARC_PKG="arc$RANDOM"
    export ARC_PROFILE="$ARC_USER_HOME/.bashrc"         # chosen from $SHELL below
    mkdir -p "$ARC_USER_HOME" "$ARC_WORK"
}

teardown_file() {
    rm -rf "$ARC_ROOT"
}

setup() {
    export SCRIPTICUS_HOME="$ARC_SCRIPTICUS_HOME"
    export HOME="$ARC_USER_HOME"
    export SHELL=/bin/bash          # so the profile choice is ~/.bashrc
    # Whatever the bootstrap wrote into the profile is how a real shell picks
    # the bin directory up. Sourcing it is the restart the client asks for —
    # and means every step below runs commands found the way a user finds them,
    # never via a PATH this harness arranged.
    [ -f "$ARC_PROFILE" ] && . "$ARC_PROFILE"
    cd "$ARC_WORK"
}

@test "arc 1: the machine starts with no client state at all" {
    [ ! -d "$SCRIPTICUS_HOME" ]
    [ ! -f "$ARC_PROFILE" ]
    # The package name is unique to this file, so nothing below can collide
    # with another spec publishing into the same Gitea.
    run scripticus list --installed
    [ "$status" -eq 0 ]
    [[ "$output" != *"$ARC_PKG"* ]]
}

@test "arc 2: login registers the remote and stores a verified token" {
    run bash -c "printf '%s\n' \"$SCRIPTICUS_E2E_TOKEN\" | scripticus login origin '$SCRIPTICUS_E2E_URL'"
    [ "$status" -eq 0 ]
    [[ "$output" == *"Logged in to origin"* ]]

    # One command did both halves (D35): the remote is registered too.
    run scripticus config remote list
    [ "$status" -eq 0 ]
    [[ "$output" == *"origin"* ]]
}

@test "arc 3: new scaffolds a package, and the author adds a second command" {
    run scripticus new bash "$ARC_PKG" -n "$SCRIPTICUS_E2E_NAMESPACE"
    [ "$status" -eq 0 ]
    [ -f "$ARC_PKG/meta.toml" ]
    [ -f "$ARC_PKG/src/main.sh" ]

    # A second entry point, declared explicitly — the [commands] table a
    # multi-command package needs (D38). What the scaffold produced is the
    # default single-command form.
    printf '#!/usr/bin/env bash\necho "%s reporting"\n' "$ARC_PKG" \
        > "$ARC_PKG/src/report.sh"
    chmod +x "$ARC_PKG/src/report.sh"
    printf '\n[commands]\n%s = "src/main.sh"\n%s-report = "src/report.sh"\n' \
        "$ARC_PKG" "$ARC_PKG" >> "$ARC_PKG/meta.toml"
}

@test "arc 4: pack produces a distributable archive" {
    run scripticus pack "$ARC_PKG" -o builds
    [ "$status" -eq 0 ]
    run bash -c "ls builds/${ARC_PKG}-0.1.0-*.tar.gz"
    [ "$status" -eq 0 ]
}

@test "arc 5: publish uploads the version to the remote" {
    # No SCRIPTICUS_TOKEN: this authenticates from the credential store that
    # arc 2 populated, which is how a person publishes.
    run scripticus publish "builds/${ARC_PKG}-0.1.0"
    [ "$status" -eq 0 ]
    [[ "$output" == *"Published ${ARC_PKG} 0.1.0"* ]]
}

@test "arc 6: the published package is discoverable by identity and by content" {
    local ns="$SCRIPTICUS_E2E_NAMESPACE"

    run scripticus list "${ns}/${ARC_PKG}"
    [ "$status" -eq 0 ]
    [[ "$output" == *"${ns}/${ARC_PKG}"* ]]
    [[ "$output" == *"0.1.0"* ]]

    run scripticus search "$ARC_PKG"
    [ "$status" -eq 0 ]
    [[ "$output" == *"$ARC_PKG"* ]]
}

@test "arc 7: install bootstraps PATH on a machine that never ran init (D63)" {
    local ns="$SCRIPTICUS_E2E_NAMESPACE"
    [ ! -f "$ARC_PROFILE" ]   # nothing has touched the profile yet

    run scripticus install "${ns}/${ARC_PKG}" --yes
    [ "$status" -eq 0 ]

    # The install did init's job and said so, rather than leaving shims in a
    # directory nothing searches.
    [ -f "$ARC_PROFILE" ]
    grep -q "$SCRIPTICUS_HOME/bin" "$ARC_PROFILE"
    grep -q "SCRIPTICUS_LIB" "$ARC_PROFILE"
    [[ "$(printf '%s' "$output" | tr -d ' \n')" == *"restartyourshell"* ]]
}

@test "arc 8: after the shell restart, every command shim runs" {
    local ns="$SCRIPTICUS_E2E_NAMESPACE"
    # setup() sourced the profile arc 7 wrote — that, and nothing else, is why
    # these resolve.
    run "$ARC_PKG"
    [ "$status" -eq 0 ]
    [[ "$output" == *"Hello from ${ARC_PKG}!"* ]]

    run "${ARC_PKG}-report"
    [ "$status" -eq 0 ]
    [[ "$output" == *"${ARC_PKG} reporting"* ]]

    # The <ns>.<pkg>.<cmd> form is guaranteed unique and always present (D38).
    run "${ns}.${ARC_PKG}.${ARC_PKG}"
    [ "$status" -eq 0 ]
    [[ "$output" == *"Hello from ${ARC_PKG}!"* ]]
}

@test "arc 9: update floats the install to a newly published version" {
    local ns="$SCRIPTICUS_E2E_NAMESPACE"

    # The author ships 0.2.0 from the same source tree.
    sed -i 's/^version = .*/version = "0.2.0"/' "$ARC_PKG/meta.toml"
    run scripticus pack "$ARC_PKG" -o builds
    [ "$status" -eq 0 ]
    run scripticus publish "builds/${ARC_PKG}-0.2.0"
    [ "$status" -eq 0 ]

    run scripticus update "$ARC_PKG" --yes
    [ "$status" -eq 0 ]

    run scripticus list --installed "${ns}/${ARC_PKG}"
    [ "$status" -eq 0 ]
    [[ "$output" == *"0.2.0"* ]]
}

@test "arc 10: uninstall removes the package and every shim it owned" {
    local ns="$SCRIPTICUS_E2E_NAMESPACE"

    run scripticus uninstall "${ns}/${ARC_PKG}" -y
    [ "$status" -eq 0 ]

    run bash -c "command -v $ARC_PKG"
    [ "$status" -ne 0 ]
    run bash -c "command -v ${ARC_PKG}-report"
    [ "$status" -ne 0 ]

    run scripticus list --installed "${ns}/${ARC_PKG}"
    [ "$status" -eq 0 ]
    [[ "$output" != *"${ns}/${ARC_PKG}"* ]]
}

@test "arc 11: init afterwards is a no-op, since install already did its work" {
    # `init` remains for people who want the PATH change before installing
    # anything (D63); running it on a bootstrapped machine must change nothing.
    run scripticus init
    [ "$status" -eq 0 ]
    [[ "$output" == *"already"* ]]

    [ "$(grep -c "$SCRIPTICUS_HOME/bin" "$ARC_PROFILE")" -eq 1 ]
}
