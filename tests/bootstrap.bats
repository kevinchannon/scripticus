#!/usr/bin/env bats
# get-scripticus-svr's decision logic (D61): the host preflight, the three
# refusals, and the project-name derivation that decides whether two stacks
# collide.
#
# These specs stub `docker` rather than driving a real one. The script's job at
# this level is to *decide* — is this host capable, is something already here,
# what is this stack called — and those branches are exactly the ones a real
# stack makes slow and awkward to reach (a too-old compose plugin cannot be
# conjured; an "already exists" refusal needs something already existing).
# A stub reaches every branch in milliseconds and with no shared state.
#
# What this deliberately does not cover: the happy path. The script polls
# `http://localhost:<port>/accounts/`, which assumes it runs on the Docker host —
# inside the DooD e2e runner, localhost is the runner, not the host publishing
# those ports. The stand-up it performs is the same one scripts/start-server
# drives for every other spec in this suite, so the bundle itself is covered;
# what is not is this script's own orchestration of it end to end.
#
# Unlike the rest of the suite these need no registry and no SCRIPTICUS_E2E_*.

setup() {
    SCRIPT="$BATS_TEST_DIRNAME/../get-scripticus-svr"
    [ -f "$SCRIPT" ]

    WORK="$BATS_TEST_TMPDIR/work"
    STUB="$BATS_TEST_TMPDIR/stub"
    mkdir -p "$WORK" "$STUB"

    # A `docker` that answers only what the preflight asks, steered by env:
    #   FAKE_DOCKER_INFO_FAIL=1   daemon unreachable
    #   FAKE_COMPOSE_MISSING=1    no compose v2 plugin
    #   FAKE_COMPOSE_VERSION      what `compose version --short` reports
    #   FAKE_EXISTING_PROJECT     the one project name `compose ps -aq` finds
    cat > "$STUB/docker" <<'STUBEOF'
#!/bin/sh
case "$1" in
  info)
    if [ "${FAKE_DOCKER_INFO_FAIL:-}" = "1" ]; then exit 1; fi
    exit 0
    ;;
  compose)
    shift
    if [ "$1" = "version" ]; then
      if [ "${FAKE_COMPOSE_MISSING:-}" = "1" ]; then exit 1; fi
      printf '%s\n' "${FAKE_COMPOSE_VERSION:-2.30.0}"
      exit 0
    fi
    project=""
    if [ "$1" = "-p" ]; then project="$2"; shift 2; fi
    if [ "$1" = "ps" ]; then
      if [ -n "${FAKE_EXISTING_PROJECT:-}" ] && [ "$project" = "$FAKE_EXISTING_PROJECT" ]; then
        printf 'c0ffee1234\n'
      fi
      exit 0
    fi
    exit 0
    ;;
esac
exit 0
STUBEOF
    chmod +x "$STUB/docker"

    # A `curl` too, so the download is ours to control rather than the test
    # host's: the runner image ships busybox wget, which refuses file:// URLs,
    # and a test whose download fails for an ambient reason asserts nothing.
    #   FAKE_FETCH_FAIL=0     the download succeeds
    #   FAKE_COMPOSE_CONTENT  what it returns
    # It fails by default: a run reaching the download has passed preflight and
    # every refusal, which is what most of these specs are actually asserting,
    # and stopping there keeps them from starting a stack and then waiting out
    # a three-minute Gitea poll that can never succeed.
    cat > "$STUB/curl" <<'STUBEOF'
#!/bin/sh
if [ "${FAKE_FETCH_FAIL:-1}" = "1" ]; then exit 22; fi
printf '%s' "${FAKE_COMPOSE_CONTENT:-}"
exit 0
STUBEOF
    chmod +x "$STUB/curl"

    export PATH="$STUB:$PATH"
    export SCRIPTICUS_COMPOSE_URL="https://example.invalid/docker-compose.yml"

    cd "$WORK"
}

@test "--help lists the options and exits cleanly" {
    run sh "$SCRIPT" --help
    [ "$status" -eq 0 ]
    [[ "$output" == *"--dir"* ]]
    [[ "$output" == *"--public-url"* ]]
    [[ "$output" == *"SCRIPTICUS_COMPOSE_URL"* ]]
}

@test "an unknown option fails with the usage message" {
    run sh "$SCRIPT" --definitely-not-an-option
    [ "$status" -ne 0 ]
    [[ "$output" == *"unknown option"* ]]
    [[ "$output" == *"Usage:"* ]]
}

@test "an option missing its value fails rather than silently defaulting" {
    run sh "$SCRIPT" --dir
    [ "$status" -ne 0 ]
    [[ "$output" == *"--dir needs a value"* ]]
}

@test "a missing docker is reported with somewhere to go" {
    # A PATH with the stub removed and no real docker on it.
    run env PATH="/usr/bin:/bin" sh "$SCRIPT" --dir "$WORK/reg"
    [ "$status" -ne 0 ]
    [[ "$output" == *"docker is not installed"* ]]
    [[ "$output" == *"docs.docker.com"* ]]
    [ ! -e "$WORK/reg" ]
}

@test "an unreachable docker daemon is distinguished from a missing docker" {
    FAKE_DOCKER_INFO_FAIL=1 run sh "$SCRIPT" --dir "$WORK/reg"
    [ "$status" -ne 0 ]
    [[ "$output" == *"daemon is not reachable"* ]]
    [ ! -e "$WORK/reg" ]
}

@test "a missing compose v2 plugin is reported" {
    FAKE_COMPOSE_MISSING=1 run sh "$SCRIPT" --dir "$WORK/reg"
    [ "$status" -ne 0 ]
    [[ "$output" == *"compose v2 plugin is missing"* ]]
}

@test "a compose older than the inline-config requirement is refused by version" {
    # 2.23.0 is one patch below the 2.23.1 that understands `configs.content`
    # (D45) — the boundary is the point, so test the near miss, not 1.0.
    FAKE_COMPOSE_VERSION=2.23.0 run sh "$SCRIPT" --dir "$WORK/reg"
    [ "$status" -ne 0 ]
    [[ "$output" == *"2.23.1 or newer is required"* ]]
    [[ "$output" == *"2.23.0"* ]]
    [ ! -e "$WORK/reg" ]
}

@test "the minimum supported compose version is accepted" {
    FAKE_COMPOSE_VERSION=2.23.1 run sh "$SCRIPT" --dir "$WORK/reg"
    # Passes preflight and dies at the unreachable download instead.
    [ "$status" -ne 0 ]
    [[ "$output" != *"is required"* ]]
    [[ "$output" == *"could not download"* ]]
}

@test "a v-prefixed compose version is compared numerically" {
    FAKE_COMPOSE_VERSION=v2.40.1 run sh "$SCRIPT" --dir "$WORK/reg"
    [ "$status" -ne 0 ]
    [[ "$output" == *"could not download"* ]]
}

@test "a two-field compose version does not fail the comparison" {
    FAKE_COMPOSE_VERSION=2.30 run sh "$SCRIPT" --dir "$WORK/reg"
    [ "$status" -ne 0 ]
    [[ "$output" == *"could not download"* ]]
}

@test "a non-empty target directory is refused, not written into" {
    mkdir -p "$WORK/taken"
    echo "somebody else's file" > "$WORK/taken/keep-me"

    run sh "$SCRIPT" --dir "$WORK/taken"
    [ "$status" -ne 0 ]
    [[ "$output" == *"already exists and is not empty"* ]]
    # The refusal is the whole point: the existing content is untouched.
    [ "$(cat "$WORK/taken/keep-me")" = "somebody else's file" ]
    [ ! -f "$WORK/taken/docker-compose.yml" ]
}

@test "an existing but empty target directory is acceptable" {
    mkdir -p "$WORK/empty"
    run sh "$SCRIPT" --dir "$WORK/empty"
    [ "$status" -ne 0 ]
    [[ "$output" != *"already exists"* ]]
    [[ "$output" == *"could not download"* ]]
}

@test "an existing compose stack of the same name is refused" {
    FAKE_EXISTING_PROJECT=myreg run sh "$SCRIPT" --dir "$WORK/myreg"
    [ "$status" -ne 0 ]
    [[ "$output" == *"'myreg' compose stack already exists"* ]]
    [[ "$output" == *"down -v"* ]]
}

@test "a stack under a different name does not block the install" {
    FAKE_EXISTING_PROJECT=someone-elses run sh "$SCRIPT" --dir "$WORK/myreg"
    [ "$status" -ne 0 ]
    [[ "$output" != *"already exists"* ]]
    [[ "$output" == *"could not download"* ]]
}

@test "the project name is derived from the directory, so --dir gives an independent stack" {
    # Compose's own rule: lowercased, with characters outside [a-z0-9_-]
    # replaced. Getting this wrong is what makes a second registry silently
    # adopt the first one's containers.
    # Separate directories: a failed run is expected to leave nothing behind,
    # but reusing one here would confuse "refused for the project name" with
    # "refused for the directory".
    FAKE_EXISTING_PROJECT=my.registry run sh "$SCRIPT" --dir "$WORK/a/My.Registry"
    [ "$status" -ne 0 ]
    # The dot is not legal in a project name, so it cannot have matched.
    [[ "$output" != *"already exists"* ]]

    FAKE_EXISTING_PROJECT=my-registry run sh "$SCRIPT" --dir "$WORK/b/My.Registry"
    [ "$status" -ne 0 ]
    [[ "$output" == *"'my-registry' compose stack already exists"* ]]
}

@test "a directory that cannot start a project name is trimmed, not prefixed" {
    # Compose project names must begin with [a-z0-9]. Trimming the leading
    # remainder gives 'registry'; bolting a prefix on would give a name with
    # the project's own name doubled up in front of it.
    FAKE_EXISTING_PROJECT=registry run sh "$SCRIPT" --dir "$WORK/.registry"
    [ "$status" -ne 0 ]
    [[ "$output" == *"'registry' compose stack already exists"* ]]
}

@test "a directory named only in punctuation still yields a usable project name" {
    FAKE_EXISTING_PROJECT=scripticus run sh "$SCRIPT" --dir "$WORK/..."
    [ "$status" -ne 0 ]
    [[ "$output" == *"'scripticus' compose stack already exists"* ]]
}

@test "a common install directory is not given a redundant prefix" {
    # 'scripticus-svr' is what most people will call it; the project name
    # should be that, not 'scripticus-scripticus-svr'.
    FAKE_EXISTING_PROJECT=scripticus-svr run sh "$SCRIPT" --dir "$WORK/scripticus-svr"
    [ "$status" -ne 0 ]
    [[ "$output" == *"'scripticus-svr' compose stack already exists"* ]]
}

@test "a trailing slash on --dir does not produce an empty project name" {
    FAKE_EXISTING_PROJECT=reg run sh "$SCRIPT" --dir "$WORK/reg/"
    [ "$status" -ne 0 ]
    [[ "$output" == *"'reg' compose stack already exists"* ]]
}

@test "a download failure names the URL it tried" {
    run sh "$SCRIPT" --dir "$WORK/reg"
    [ "$status" -ne 0 ]
    [[ "$output" == *"could not download"* ]]
    [[ "$output" == *"https://example.invalid/docker-compose.yml"* ]]
}

@test "a failed download leaves nothing behind" {
    # The next attempt would otherwise find the directory this one made and
    # refuse to use it — a failure that makes itself permanent.
    run sh "$SCRIPT" --dir "$WORK/reg"
    [ "$status" -ne 0 ]
    [ ! -e "$WORK/reg" ]
}

@test "a failed download into an existing empty directory keeps the directory" {
    # It was not ours to remove — only the compose file we wrote goes.
    mkdir -p "$WORK/mine"
    run sh "$SCRIPT" --dir "$WORK/mine"
    [ "$status" -ne 0 ]
    [ -d "$WORK/mine" ]
    [ ! -f "$WORK/mine/docker-compose.yml" ]
}

@test "an empty compose file is rejected rather than started" {
    # A proxy, a captive portal, or a moved file can all return 200 and nothing.
    FAKE_FETCH_FAIL=0 FAKE_COMPOSE_CONTENT="" run sh "$SCRIPT" --dir "$WORK/reg"
    [ "$status" -ne 0 ]
    [[ "$output" == *"empty compose file"* ]]
    [ ! -e "$WORK/reg" ]
}

@test "--dir skips the directory prompt entirely" {
    # With --dir there is nothing to ask, so the run never touches /dev/tty and
    # cannot block — which is what makes the script safe in a script.
    run sh "$SCRIPT" --dir "$WORK/reg"
    [ "$status" -ne 0 ]
    [[ "$output" != *"Where should the registry live"* ]]
    [[ "$output" == *"could not download"* ]]
}

@test "with no controlling terminal the default directory is used, not a hang" {
    command -v setsid >/dev/null || skip "needs setsid to drop the controlling terminal"

    # No tty means no prompt: the script must fall through to its default
    # rather than block forever on a question nobody can see.
    run timeout 30 setsid sh "$SCRIPT"
    [ "$status" -ne 0 ]
    [ "$status" -ne 124 ]  # 124 is timeout's — i.e. it hung on the prompt
    [[ "$output" != *"Where should the registry live"* ]]
    [[ "$output" == *"could not download"* ]]
}
