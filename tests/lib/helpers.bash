# Shared setup for the Scripticus e2e BATS suite. Every test drives the real
# `scripticus` binary (installed from the built wheels by tests/e2e.sh) against
# the live registry bundle over the D45 front URL. See tests/README.md.

# Per-test client state: a fresh SCRIPTICUS_HOME (config, credentials, lockfile,
# bin) and a scratch working directory off the repo tree, so tests never write
# into it. Isolation matters — each test starts with nothing installed and no
# remotes. The SCRIPTICUS_E2E_* env is exported by tests/e2e.sh.
common_setup() {
    : "${SCRIPTICUS_E2E_URL:?should be exported by tests/e2e.sh}"
    : "${SCRIPTICUS_E2E_TOKEN:?should be exported by tests/e2e.sh}"
    : "${SCRIPTICUS_E2E_NAMESPACE:?should be exported by tests/e2e.sh}"

    export SCRIPTICUS_HOME="$BATS_TEST_TMPDIR/home"
    mkdir -p "$SCRIPTICUS_HOME"
    # Installed command shims land in $SCRIPTICUS_HOME/bin; putting it on PATH
    # is exactly what `scripticus init` bootstraps into a user's shell profile.
    export PATH="$SCRIPTICUS_HOME/bin:$PATH"

    WORK="$BATS_TEST_TMPDIR/work"
    mkdir -p "$WORK"
    cd "$WORK"
}

# Gitea persists across the whole `bats` run, so republishing the same identity
# collides between tests. Each test authors a uniquely-named package.
unique_pkg() {
    echo "pkg${BATS_SUITE_TEST_NUMBER:-0}x${RANDOM}"
}

# Register + authenticate the 'origin' remote, feeding the token on stdin the
# way a user answers the hidden prompt. Run via `run do_login`.
do_login() {
    printf '%s\n' "$SCRIPTICUS_E2E_TOKEN" \
        | scripticus login origin "$SCRIPTICUS_E2E_URL"
}

# Scaffold a bash package, stamp a version into its manifest, pack it, and
# publish every produced archive. Publish auth comes from SCRIPTICUS_TOKEN,
# set inline from the bootstrapped SCRIPTICUS_E2E_TOKEN.
# Usage: author_and_publish <name> <version>
author_and_publish() {
    local name="$1" version="$2"
    scripticus new bash "$name" -n "$SCRIPTICUS_E2E_NAMESPACE"
    sed -i "s/^version = .*/version = \"$version\"/" "$name/meta.toml"
    scripticus pack "$name" -o builds
    SCRIPTICUS_TOKEN="$SCRIPTICUS_E2E_TOKEN" \
        scripticus publish "builds/$name-$version"
}

# Author + publish a bash package with an explicit [commands] table, one
# command per (name, output) pair, each printing its output so tests can tell
# which shim ran. Usage:
#   author_and_publish_cmds <name> <version> <cmd> <output> [<cmd> <output> ...]
author_and_publish_cmds() {
    local name="$1" version="$2"
    shift 2
    local manifest="$name/meta.toml"
    mkdir -p "$name/src"
    {
        printf '[package]\n'
        printf 'namespace = "%s"\n' "$SCRIPTICUS_E2E_NAMESPACE"
        printf 'name = "%s"\n' "$name"
        printf 'version = "%s"\n' "$version"
        printf 'language = "bash"\n'
        printf 'description = "e2e fixture"\n\n'
        printf '[platforms]\nos = ["linux", "macos"]\n\n'
        printf '[commands]\n'
    } > "$manifest"
    while [ "$#" -gt 0 ]; do
        local cmd="$1" out="$2"
        shift 2
        printf '%s = "src/%s.sh"\n' "$cmd" "$cmd" >> "$manifest"
        printf '#!/usr/bin/env bash\necho "%s"\n' "$out" > "$name/src/$cmd.sh"
        chmod +x "$name/src/$cmd.sh"
    done
    scripticus pack "$name" -o builds
    SCRIPTICUS_TOKEN="$SCRIPTICUS_E2E_TOKEN" \
        scripticus publish "builds/$name-$version"
}
