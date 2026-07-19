# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Current state

Implementation has just begun. The repo is a **uv workspace** (Cargo-style)
with three members: `client/` (PyPI package `scripticus`, the CLI),
`server/` (PyPI package `scripticus-server`, providing the `scripticus-svr`
command; will become the FastAPI index service fronting Gitea per D13), and
`schema/` (PyPI package `scripticus-schema`, the shared client/server
contract, D29). The client is a Typer + Rich CLI. It implements `-v`/`--version`, `new`
(scaffolding, `scaffold.py`), `pack` (archive creation, `pack.py`), and
`install -f` (local install: extraction, transaction flow, shims, lockfile —
`install.py`; a package declaring package dependencies is rejected by a
resolver stub until remote install brings real resolution), `uninstall`
(lockfile-driven removal of a package's files and owned shims, with a
replacement picker for commands other installed packages still provide, D28
— `uninstall.py`), and `use` (manually re-point a command shim at an
installed package, D11 — `use.py`, sharing the uninstall picker's re-point
primitive). The contract code lives in `schema/` (`scripticus_schema`):
the Pydantic manifest model and validation (`manifest.py`), the D3/D27
content hash (`treehash.py`), semver ordering (`semver.py`), and the wire
models for the read API (`index_api.py`, D30) and publish response
(`publish_api.py`, D32). Only code meeting D29's admission rule (defines
what a package is, or how client and server communicate) may go there. Client-side state goes under `~/.scripticus/`
(override with `SCRIPTICUS_HOME`, which tests rely on). The server is a
FastAPI app (`app.py`) exposing `GET /health`, `GET /version`, and the
read endpoints — `GET /packages/{namespace}/{name}` (version listing)
and `GET /search` — backed by the SQLAlchemy index data model (`db.py`,
D23; tables created via `create_all` on first use, D31; DB URL from
`SCRIPTICUS_INDEX_DB`, default a local SQLite file), plus the write path:
`POST /packages` (`publish.py`, D32 — pass-through Gitea auth against
`SCRIPTICUS_GITEA_URL`, everything derived from the uploaded archive,
blob to Gitea before the index record commits; dependency rules per D33),
with the Gitea boundary isolated in `gitea.py` so tests fake it
(e2e tests against real Gitea are marked `e2e`, deselected by default,
run by `.github/workflows/e2e.yml`). The server has
no Typer CLI — `scripticus-svr` (`main.py`, argparse for
`--host`/`--port`) prints a version/address banner and runs uvicorn, and
the OpenAPI spec is served at `/openapi.json` rather than committed to
the repo. Resolution does not exist yet. A server `Dockerfile` exists,
and the root `docker-compose.yml` is the two-service registry bundle
(index service + Gitea). The design docs below
describe the intended v1.0.0
and remain the source of truth for architecture.

## Commands

All run from the repository root:

```console
$ uv sync                          # create/update the workspace environment
$ uv run pytest                    # run all tests
$ uv run pytest client/tests/test_cli.py::test_bare_invocation_shows_help
                                   # run a single test
$ uv run scripticus -v             # run the client CLI
$ uv run scripticus-svr            # start the index service (Ctrl-C to stop)
$ uv build --package scripticus    # build the client wheel/sdist into dist/
$ uv build --package scripticus-server
```

## Releasing

Releases are tag-driven, one tag per package: pushing `client-vX.Y.Z`
releases `scripticus` to PyPI, `schema-vX.Y.Z` releases `scripticus-schema`
(`.github/workflows/release.yml` — one independent run per pushed tag, so
tagging several packages at once is fine). The tag's version is stamped
into the member's `pyproject.toml` at build time. A client release waits
for a PyPI `scripticus-schema` satisfying the client's pin before
publishing (D29); the pipx-install validation runs only for `client-v*`
releases. A `server-v*` tag additionally pushes a Docker image to
`kevinchannon/scripticus-server` (version + `latest` tags) after the PyPI
publish succeeds; this needs the `DOCKERHUB_USERNAME`/`DOCKERHUB_TOKEN`
repo secrets.

## Code layout

- Workspace root [pyproject.toml](pyproject.toml) is virtual (no `[project]`
  table) — it declares workspace members and shared pytest config (importlib
  import mode, so same-named test modules in different members coexist).
- `schema/` is a dependency of the other members, declared as a normal PyPI
  dependency with tight same-minor bounds plus a `[tool.uv.sources]`
  workspace source (D29). Built wheels do not vendor it: `scripticus-schema`
  must be published to PyPI before any client/server release that bumps its
  pin.
- `client/` and `server/` are structured identically (src layout, `uv_build`
  backend): a Typer app in `cli.py` mapped to the console script
  (`scripticus` → `scripticus.cli:app`, `scripticus-svr` →
  `scripticus_server.cli:app`), tests in `<member>/tests/` using Typer's
  `CliRunner`, pytest in the member's `dev` dependency group.
- Each member's version has a single source: `[project.version]` in its
  `pyproject.toml`, read at runtime via `importlib.metadata`. The checked-in
  value is always the placeholder `0.0.0.dev0` — obviously not a release —
  because real versions exist only in release artifacts, stamped from the
  tag by the release workflow.
- README split: the root [README.md](README.md) is the project overview and
  developer guide; [client/README.md](client/README.md) is the client's PyPI
  page (install/usage/authoring docs); [server/README.md](server/README.md)
  is the server's PyPI page (registry standup docs).

## Documents and their roles

- [README.md](README.md) — user-facing description of the intended product
  (CLI usage, manifest format, server setup).
- [doc/VISION.md](doc/VISION.md) — two-paragraph purpose statement.
- [doc/ROADMAP.md](doc/ROADMAP.md) — v1.0.0 scope as a checklist, plus
  deliberately unscheduled post-v1 items.
- [doc/ARCHITECTURE.md](doc/ARCHITECTURE.md) — components, data flows, index
  data model.
- [doc/DECISIONS.md](doc/DECISIONS.md) — the decision record (D1–D29). Each
  entry has decision, reasoning, and consequences (good *and* bad).

The decision record is the backbone: architecture and roadmap statements
trace back to numbered decisions. When changing a design, update or add a
DECISIONS.md entry (following its format, numbered sequentially) and keep the
other docs consistent with it — they cross-reference by decision number.

## Load-bearing design decisions

These are the choices the rest of the design hangs off; don't contradict them
casually:

- **Gitea is the substrate** (D2): storage, auth, and namespace ownership are
  Gitea's job. The index service owns only manifest-derived data; nothing
  ACL-shaped is ever cached — publish re-checks permissions against Gitea
  live (D24).
- **Content-addressed identity** (D3): an artifact's canonical identity is a
  Merkle-style hash of the package directory tree. Name/version/variant are
  index metadata pointing at content.
- **Everything is namespaced** (D4/D5): no flat namespace anywhere in
  storage; bare names are purely a client-side search-path convenience.
- **Atomic server-mediated publish, direct blob download** (D8/D9): writes go
  through the index service (all-or-nothing); reads fetch blobs straight from
  Gitea.
- **Verbatim manifest is authoritative** (D21): extracted relational columns
  are a re-derivable projection, never independently editable (crates.io
  pattern).
- **No manifest correctness verification, ever** (D14): no lint, no sandbox,
  no advisory checks — a deliberate non-goal, not a gap to fill.
- **Single shared bin dir, last-install-wins shims** (D11), with the
  dnf-style transaction flow (D17) and split `--force` semantics (D18) as the
  safety mechanisms. Exit codes never mean "partially installed".
- **SQLite via SQLAlchemy with no SQLite-isms** (D23), so Postgres stays a
  configuration change.

Deliberately not designed yet (per ARCHITECTURE.md): auth token scoping
for CI publishing and the resolver algorithm's internals. The read- and
write-path API schemas are designed (D30, D32).
