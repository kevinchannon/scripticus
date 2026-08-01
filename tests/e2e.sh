#!/usr/bin/env bash
# The end-to-end run, executed INSIDE the Tasktree containerised e2e runner
# (`tt e2e-test`). The runner has the host Docker socket mounted (DooD) and the
# repo auto-mounted at the working directory, and the `build` task has already
# produced the client wheels in dist/.
#
# The registry bundle stand-up + test-user bootstrap is shared with
# `tt start-server` and lives in scripts/start-server; here we drive it in e2e
# mode (no host ports, join the stack network, reach services by name), install
# the client, run the BATS specs on top, and tear the stack back down.
#
#   tt e2e-test                  # build wheels (dep), stand up, test, tear down
#   KEEP_UP=1 tt e2e-test        # leave the stack + network for debugging
set -euo pipefail

PROJECT="scripticus-e2e"
NETWORK="${PROJECT}_default"
NAMESPACE="scripticus-e2e"          # the Gitea user; fixtures publish under it
ORG="scripticus-e2e-org"            # an organisation namespace (D4), not a user
ORG_USER="scripticus-e2e-publisher" # a member of ORG's publishing team, not an owner
PASSWORD="e2e-password-1"
FRONT_URL="http://proxy"            # the single front URL, by service name (D45)
COMPOSE_FILES="-f docker-compose.yml -f tests/docker-compose.build.yml -f tests/docker-compose.e2e.yml"
# shellcheck disable=SC2086
COMPOSE=(docker compose -p "$PROJECT" $COMPOSE_FILES)
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

# Stand the bundle up + bootstrap the test user via the shared script, in e2e
# mode: build from source, no host ports, join the stack network, reach Gitea
# and the proxy by service name. The token comes back in a file.
echo ">> standing up the registry bundle + test user"
TOKEN_FILE="$(mktemp)"
ORG_TOKEN_FILE="$(mktemp)"
ORG_NARROW_TOKEN_FILE="$(mktemp)"
SC_PROJECT="$PROJECT" \
SC_COMPOSE_FILES="$COMPOSE_FILES" \
SC_GITEA_URL="http://gitea:3000" \
SC_FRONT_URL="$FRONT_URL" \
SC_NAMESPACE="$NAMESPACE" \
SC_PASSWORD="$PASSWORD" \
SC_JOIN_NETWORK="$NETWORK" \
SC_TOKEN_FILE="$TOKEN_FILE" \
SC_ORG="$ORG" \
SC_ORG_USER="$ORG_USER" \
SC_ORG_TOKEN_FILE="$ORG_TOKEN_FILE" \
SC_ORG_NARROW_TOKEN_FILE="$ORG_NARROW_TOKEN_FILE" \
    scripts/start-server >/dev/null

echo ">> running the BATS suite"
export SCRIPTICUS_E2E_URL="$FRONT_URL"
export SCRIPTICUS_E2E_TOKEN="$(cat "$TOKEN_FILE")"
export SCRIPTICUS_E2E_NAMESPACE="$NAMESPACE"
# The organisation half: a namespace that is not the publishing user, so the
# Gitea ACL call actually happens, plus a token missing read:organization.
export SCRIPTICUS_E2E_ORG="$ORG"
export SCRIPTICUS_E2E_ORG_USER="$ORG_USER"
export SCRIPTICUS_E2E_ORG_TOKEN="$(cat "$ORG_TOKEN_FILE")"
export SCRIPTICUS_E2E_ORG_NARROW_TOKEN="$(cat "$ORG_NARROW_TOKEN_FILE")"
bats --print-output-on-failure --recursive tests
