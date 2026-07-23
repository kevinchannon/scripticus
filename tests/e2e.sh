#!/usr/bin/env bash
# The end-to-end run, executed INSIDE the Tasktree containerised e2e runner
# (`tt e2e-test`). The runner has the host Docker socket mounted (DooD) and the
# repo auto-mounted at the working directory, and the `build` task has already
# produced the client wheels in dist/.
#
# It stands the registry bundle (proxy + index + Gitea) up from source on the
# host daemon, joins that stack's network so it can reach services by name,
# bootstraps a Gitea user + token, and drives the real client through the BATS
# specs. The bundle publishes no host ports (the e2e overlay `!reset`s them),
# so a run never collides with a dev stack on :3000/:8000.
#
#   tt e2e-test                  # build wheels (dep), stand up, test, tear down
#   KEEP_UP=1 tt e2e-test        # leave the stack + network for debugging
set -euo pipefail

PROJECT="scripticus-e2e"
NETWORK="${PROJECT}_default"
NAMESPACE="scripticus-e2e"          # the Gitea user; fixtures publish under it
PASSWORD="e2e-password-1"
COMPOSE=(docker compose -p "$PROJECT"
         -f docker-compose.yml -f tests/docker-compose.e2e.yml)
SELF="$(hostname)"                  # this runner's container id, for network ops

cleanup() {
    if [[ "${KEEP_UP:-}" == "1" ]]; then
        echo ">> KEEP_UP=1 — leaving the stack up (docker compose -p $PROJECT down -v to clean up)"
        return
    fi
    echo ">> tearing down"
    docker network disconnect "$NETWORK" "$SELF" >/dev/null 2>&1 || true
    "${COMPOSE[@]}" down -v --remove-orphans >/dev/null 2>&1 || true
}
trap cleanup EXIT

wait_for() {
    local what="$1" url="$2" attempts="${3:-60}"
    echo ">> waiting for $what ($url)"
    for _ in $(seq 1 "$attempts"); do
        if curl -sf "$url" >/dev/null 2>&1; then return 0; fi
        sleep 2
    done
    echo "::error:: $what did not become healthy at $url" >&2
    "${COMPOSE[@]}" logs >&2 || true
    return 1
}

echo ">> installing the client from the freshly built wheels"
python3 -m venv /tmp/venv
# shellcheck disable=SC1091
. /tmp/venv/bin/activate
# The wheels carry same-minor pins on each other (>=0.3 etc.) aimed at PyPI
# releases that don't exist for this 0.0.0.dev0 dev tree, so install the three
# workspace wheels without dependency resolution, then their third-party
# runtime deps separately. The dep list mirrors the non-internal `dependencies`
# in client/ and schema/ pyproject.toml (common has none); if those change,
# this line does too, and a miss surfaces as an import error in the specs.
pip install --no-cache-dir --quiet --no-deps dist/*.whl
pip install --no-cache-dir --quiet typer rich httpx pydantic

echo ">> building + starting the registry bundle (from source)"
"${COMPOSE[@]}" build
"${COMPOSE[@]}" up -d gitea index proxy

# DooD payoff: join the stack's network, then reach services by name directly
# (no `compose run` curl proxying, no host ports).
echo ">> joining the stack network ($NETWORK)"
docker network connect "$NETWORK" "$SELF"

wait_for "Gitea" "http://gitea:3000/api/healthz"

echo ">> bootstrapping Gitea user + token"
# A brand-new Gitea has no users and no token, and a namespace *is* a Gitea
# user (D2/D4), so provision one before the specs run.
"${COMPOSE[@]}" exec -T -u git gitea \
    gitea admin user create \
    --username "$NAMESPACE" --password "$PASSWORD" \
    --email "$NAMESPACE@example.invalid" --must-change-password=false
# write:package to publish, read:user so `login` can verify via /whoami (D40/D41).
TOKEN=$(curl -sf -u "$NAMESPACE:$PASSWORD" \
    -X POST -H 'Content-Type: application/json' \
    -d '{"name":"e2e","scopes":["write:package","read:user"]}' \
    "http://gitea:3000/api/v1/users/$NAMESPACE/tokens" \
    | python3 -c 'import json,sys; print(json.load(sys.stdin)["sha1"])')

wait_for "index (via proxy)" "http://proxy/health"

echo ">> running the BATS suite"
export SCRIPTICUS_E2E_URL="http://proxy"      # the single front URL (D45)
export SCRIPTICUS_E2E_TOKEN="$TOKEN"
export SCRIPTICUS_E2E_NAMESPACE="$NAMESPACE"
bats --print-output-on-failure --recursive tests
