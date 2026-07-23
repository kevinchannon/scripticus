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

COPY --from=docker /usr/local/bin/docker /usr/local/bin/docker
COPY --from=docker /usr/local/libexec/docker/cli-plugins/docker-compose \
     /usr/local/lib/docker/cli-plugins/docker-compose

WORKDIR /repo
