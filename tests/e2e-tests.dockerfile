# The e2e test *runner* image (Tasktree containerised runner, DooD).
#
# Toolchain only — deliberately NO scripticus client baked in. The client is
# installed at test time from the wheels the `build` task drops in dist/ (which
# Tasktree volume-maps in with the rest of the repo), so this image is stable:
# it rebuilds only when the toolchain changes, never when the project source
# does. It carries what tests/e2e.sh needs to stand the server bundle up on the
# host's Docker daemon (mounted socket) and drive the client with BATS:
#
#   * docker CLI + compose plugin — to `compose up` the bundle (DooD)
#   * python + venv                — to pip-install the client wheels
#   * bats, curl, git              — to run the specs and talk to the stack
#
# docker/compose come from the official docker:cli image rather than an apt
# repo dance; everything else is Debian so the client's wheels (pydantic-core
# et al.) install from plain manylinux with no musl surprises.
FROM docker:27-cli AS docker

FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends bats curl ca-certificates git \
    && rm -rf /var/lib/apt/lists/*

# Give the image a passwd entry for the host user Tasktree maps in with
# `--user <uid>:<gid>`. Numeric mapping alone gets file ownership right but
# leaves the UID nameless, so `id -un`, `whoami`, and `$HOME` fail — and
# `get-scripticus-svr` defaults its admin account to `id -un`. Guarded, so a
# base image that already ships this UID is left alone (tasktree #215).
#
# The UID/GID come from `{{ tt.uid }}`/`{{ tt.gid }}` build args in
# tasktree.yaml, so the image matches whoever is running it — 501 on a Mac,
# 1001 on a GitHub runner. Requires Tasktree >= 1.4.0.
ARG UID=1000
ARG GID=1000
RUN set -eu; \
    if ! getent passwd "$UID" >/dev/null; then \
        getent group "$GID" >/dev/null || groupadd -g "$GID" runner; \
        useradd -u "$UID" -g "$GID" -m -s /bin/bash runner; \
    fi

COPY --from=docker /usr/local/bin/docker /usr/local/bin/docker
COPY --from=docker /usr/local/libexec/docker/cli-plugins/docker-compose \
     /usr/local/lib/docker/cli-plugins/docker-compose

WORKDIR /repo
