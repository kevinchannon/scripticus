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

# Author + publish a snippet package (D58): one [snippet.<name>] section per
# distinct name, and a src/<name>.<ext> file per variant carrying its own
# recognisable body. No language, no platforms. Usage:
#   author_and_publish_snippets <name> <version> <file> <body> [<file> <body> ...]
# e.g. author_and_publish_snippets boiler 0.1.0 args.sh '# sh args' args.py '# py args'
author_and_publish_snippets() {
    local name="$1" version="$2"
    shift 2
    local manifest="$name/meta.toml"
    mkdir -p "$name/src"
    {
        printf '[package]\n'
        printf 'namespace = "%s"\n' "$SCRIPTICUS_E2E_NAMESPACE"
        printf 'name = "%s"\n' "$name"
        printf 'version = "%s"\n' "$version"
        printf 'description = "e2e snippet fixture"\n'
    } > "$manifest"
    local declared=""
    while [ "$#" -gt 0 ]; do
        local file="$1" body="$2"
        shift 2
        printf '%s\n' "$body" > "$name/src/$file"
        local snippet="${file%.*}"
        case " $declared " in
            *" $snippet "*) ;;
            *)
                printf '\n[snippet.%s]\ndescription = "%s boilerplate"\n' \
                    "$snippet" "$snippet" >> "$manifest"
                declared="$declared $snippet"
                ;;
        esac
    done
    scripticus pack "$name" -o builds
    SCRIPTICUS_TOKEN="$SCRIPTICUS_E2E_TOKEN" \
        scripticus publish "builds/$name-$version"
}

# Author + publish a library package (D57): the fieldless [library] marker and
# a src/load.sh entry point defining one function. Usage:
#   author_and_publish_library <name> <version> <language> <function> <output>
author_and_publish_library() {
    local name="$1" version="$2" language="$3" function="$4" out="$5"
    mkdir -p "$name/src"
    {
        printf '[package]\n'
        printf 'namespace = "%s"\n' "$SCRIPTICUS_E2E_NAMESPACE"
        printf 'name = "%s"\n' "$name"
        printf 'version = "%s"\n' "$version"
        printf 'language = "%s"\n' "$language"
        printf 'description = "e2e library fixture"\n\n'
        printf '[platforms]\nos = ["linux", "macos"]\n\n'
        printf '[library]\n'
    } > "$name/meta.toml"
    printf '%s() { echo "%s"; }\n' "$function" "$out" > "$name/src/load.sh"
    scripticus pack "$name" -o builds
    SCRIPTICUS_TOKEN="$SCRIPTICUS_E2E_TOKEN" \
        scripticus publish "builds/$name-$version"
}

# Author + publish a bash command package that depends on a library and calls
# into it through scr_load. Usage:
#   author_and_publish_consumer <name> <version> <library> <spec> <body>
author_and_publish_consumer() {
    local name="$1" version="$2" library="$3" spec="$4" body="$5"
    mkdir -p "$name/src"
    {
        printf '[package]\n'
        printf 'namespace = "%s"\n' "$SCRIPTICUS_E2E_NAMESPACE"
        printf 'name = "%s"\n' "$name"
        printf 'version = "%s"\n' "$version"
        printf 'language = "bash"\n'
        printf 'description = "e2e library consumer"\n\n'
        printf '[platforms]\nos = ["linux", "macos"]\n\n'
        printf '[dependencies.packages]\n'
        printf '"%s/%s" = "%s"\n' "$SCRIPTICUS_E2E_NAMESPACE" "$library" "$spec"
    } > "$name/meta.toml"
    printf '%s\n' "$body" > "$name/src/main.sh"
    chmod +x "$name/src/main.sh"
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
