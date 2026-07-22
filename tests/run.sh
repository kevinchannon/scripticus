#!/usr/bin/env bash
# End-to-end test entry point. Stands up the whole registry bundle from source
# (proxy + index + Gitea), provisions a Gitea user + token, then runs the BATS
# suite inside a client container that drives the real `scripticus` CLI against
# it over the D45 front URL. Same command locally and in CI.
#
#   tests/run.sh                 # build, stand up, test, tear down
#   KEEP_UP=1 tests/run.sh       # leave the stack running for debugging
#
# The stack publishes nothing to host ports (the e2e overlay resets them), so a
# run never collides with a dev stack already bound to :3000/:8000. Health
# checks and the Gitea bootstrap therefore happen inside the network, using the
# client-test image as a toolbox. Requires: docker (with the compose plugin)
# and python3.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

PROJECT="scripticus-e2e"
COMPOSE=(docker compose -p "$PROJECT"
         -f docker-compose.yml -f tests/docker-compose.e2e.yml)

# The bootstrapped namespace is a Gitea user; fixtures publish under it.
NAMESPACE="scripticus-e2e"
PASSWORD="e2e-password-1"

cleanup() {
    if [[ "${KEEP_UP:-}" == "1" ]]; then
        echo ">> KEEP_UP=1 set — leaving the stack running (${COMPOSE[*]} down -v to clean up)"
        return
    fi
    echo ">> tearing down"
    "${COMPOSE[@]}" down -v --remove-orphans >/dev/null 2>&1 || true
}
trap cleanup EXIT

# curl from inside the compose network (Gitea/proxy have no host ports).
# --no-deps: the target services are already up; don't restart them.
net_curl() {
    "${COMPOSE[@]}" run --rm --no-deps -T --entrypoint curl client-test "$@"
}

wait_for() {
    local what="$1" url="$2" attempts="${3:-60}"
    echo ">> waiting for $what ($url)"
    for _ in $(seq 1 "$attempts"); do
        if net_curl -sf "$url" >/dev/null 2>&1; then return 0; fi
        sleep 2
    done
    echo "::error:: $what did not become healthy at $url" >&2
    "${COMPOSE[@]}" logs >&2 || true
    return 1
}

echo ">> building images (index from server/Dockerfile, client-test harness)"
"${COMPOSE[@]}" build

echo ">> starting the registry bundle"
"${COMPOSE[@]}" up -d gitea index proxy

wait_for "Gitea" "http://gitea:3000/api/healthz"

echo ">> bootstrapping Gitea user + token"
TOKEN="$(tests/bootstrap.sh "$PROJECT" "$NAMESPACE" "$PASSWORD")"

wait_for "index (via proxy)" "http://proxy/health"

echo ">> running BATS suite in the client container"
set +e
"${COMPOSE[@]}" run --rm \
    -e SCRIPTICUS_E2E_TOKEN="$TOKEN" \
    -e SCRIPTICUS_E2E_NAMESPACE="$NAMESPACE" \
    client-test
STATUS=$?
set -e

if [[ $STATUS -eq 0 ]]; then
    echo ">> e2e passed"
else
    echo ">> e2e FAILED (exit $STATUS)"
fi
exit $STATUS
