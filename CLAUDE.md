# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Current state

Implementation has just begun. The repo is a **uv workspace** (Cargo-style)
with three members: `client/` (PyPI package `scripticus`, the CLI),
`server/` (PyPI package `scripticus-server`, providing the `scripticus-svr`
command; will become the FastAPI index service fronting Gitea per D13), and
`schema/` (PyPI package `scripticus-schema`, the shared client/server
contract, D29). Client and server are Typer + Rich CLIs. The client implements `-v`/`--version`, `new`
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
content hash (`treehash.py`), and semver ordering (`semver.py`). Only code
meeting D29's admission rule (defines what a package is, or how client and
server communicate) may go there. Client-side state goes under `~/.scripticus/`
(override with `SCRIPTICUS_HOME`, which tests rely on). The server only
implements `-v`/`--version` so far. The design docs below describe the intended v1.0.0
and remain the source of truth for architecture.

## Commands

All run from the repository root:

```console
$ uv sync                          # create/update the workspace environment
$ uv run pytest                    # run all tests
$ uv run pytest client/tests/test_cli.py::test_bare_invocation_shows_help
                                   # run a single test
$ uv run scripticus -v             # run the client CLI
$ uv run scripticus-svr -v         # run the server CLI
$ uv build --package scripticus    # build the client wheel/sdist into dist/
$ uv build --package scripticus-server
```

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
  `pyproject.toml`, read at runtime via `importlib.metadata`.
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

Deliberately not designed yet (per ARCHITECTURE.md): API schemas, auth token
scoping for CI publishing, and the resolver algorithm's internals.
