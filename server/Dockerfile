# Build from the repository root (the uv workspace), e.g.:
#   docker build -f server/Dockerfile -t scripticus-server .
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS build

WORKDIR /src
COPY . .
# The image vendors the workspace schema wheel: the exact code this server
# revision was built and tested against, so image builds never wait on a
# schema PyPI release. The published-artifact pin (D29) still governs PyPI
# installs; within one image the pair is consistent by construction, so
# the pin is dropped inside this build stage only.
RUN sed -i 's/"scripticus-schema[^"]*"/"scripticus-schema"/' server/pyproject.toml \
    && uv build --package scripticus-schema --wheel --out-dir /wheels \
    && uv build --package scripticus-server --wheel --out-dir /wheels


FROM python:3.12-slim

COPY --from=build /wheels /wheels
RUN pip install --no-cache-dir /wheels/*.whl && rm -rf /wheels

RUN useradd --create-home scripticus \
    && mkdir /data && chown scripticus /data
USER scripticus

EXPOSE 8000
CMD ["scripticus-svr", "--host", "0.0.0.0", "--port", "8000"]
