# Build from the repository root (the uv workspace), e.g.:
#   docker build -f server/Dockerfile -t scripticus-server .
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS build

WORKDIR /src
COPY . .
RUN uv build --package scripticus-server --wheel --out-dir /wheels


FROM python:3.12-slim

COPY --from=build /wheels /wheels
RUN pip install --no-cache-dir /wheels/*.whl && rm -rf /wheels

RUN useradd --create-home scripticus
USER scripticus

EXPOSE 8000
CMD ["scripticus-svr", "--host", "0.0.0.0", "--port", "8000"]
