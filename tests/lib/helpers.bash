# Shared setup for the Scripticus e2e BATS suite. Every test drives the real
# `scripticus` binary (pip-installed in the client image) against the live
# registry bundle over the D45 front URL. See tests/README.md.

# Per-test client state: a fresh SCRIPTICUS_HOME (config, credentials, lockfile,
# bin) and a writable working directory, since the repo is mounted read-only.
# Isolation matters — each test starts with nothing installed and no remotes.
common_setup() {
    : "${SCRIPTICUS_E2E_URL:?should be set by tests/docker-compose.e2e.yml}"
    : "${SCRIPTICUS_E2E_TOKEN:?should be injected by tests/run.sh}"
    : "${SCRIPTICUS_E2E_NAMESPACE:?should be injected by tests/run.sh}"

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
# which the client image inherits from SCRIPTICUS_E2E_TOKEN (set below).
# Usage: author_and_publish <name> <version>
author_and_publish() {
    local name="$1" version="$2"
    scripticus new bash "$name" -n "$SCRIPTICUS_E2E_NAMESPACE"
    sed -i "s/^version = .*/version = \"$version\"/" "$name/meta.toml"
    scripticus pack "$name" -o builds
    SCRIPTICUS_TOKEN="$SCRIPTICUS_E2E_TOKEN" \
        scripticus publish "builds/$name-$version"
}
