#!/usr/bin/env bash
# Provision a fresh Gitea with a user + publish token, and print the token on
# stdout. A brand-new Gitea (INSTALL_LOCK=true) still has no users, no org and
# no token — and a Scripticus namespace *is* a Gitea user/org (D2/D4) — so this
# has to run before any client test. Adapted from .github/workflows/e2e.yml.
#
# The e2e stack publishes no host ports, so the token is minted from inside the
# compose network (the client-test image carries curl). Everything but the
# token is printed to stderr so stdout is the token alone.
#
# Usage: tests/bootstrap.sh <compose-project> <username> <password>
set -euo pipefail

PROJECT="$1"
USERNAME="$2"
PASSWORD="$3"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"
COMPOSE=(docker compose -p "$PROJECT"
         -f docker-compose.yml -f tests/docker-compose.e2e.yml)

log() { echo "bootstrap: $*" >&2; }

log "creating Gitea user '$USERNAME'"
"${COMPOSE[@]}" exec -T -u git gitea \
    gitea admin user create \
    --username "$USERNAME" --password "$PASSWORD" \
    --email "$USERNAME@example.invalid" --must-change-password=false >&2

log "minting a publish token for '$USERNAME'"
# write:package to publish, read:user so `scripticus login` can verify the
# token against /whoami before storing it (D40/D41). Minted over the internal
# network via the client-test image's curl.
RESPONSE=$("${COMPOSE[@]}" run --rm --no-deps -T --entrypoint curl client-test \
    -sf -u "$USERNAME:$PASSWORD" \
    -X POST -H 'Content-Type: application/json' \
    -d '{"name":"e2e","scopes":["write:package","read:user"]}' \
    "http://gitea:3000/api/v1/users/$USERNAME/tokens")

log "token minted"
echo "$RESPONSE" | python3 -c 'import json,sys; print(json.load(sys.stdin)["sha1"])'
