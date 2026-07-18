# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Current state

Implementation has just begun. The repo is a **uv workspace** (Cargo-style)
that will house both the client and the server; currently the only member is
`client/`, a Typer + Rich CLI packaged as `scripticus`. The design docs below
describe the intended v1.0.0 and remain the source of truth for architecture.
Python on both sides (CLI client, and a FastAPI index service fronting
Gitea), per decision D13.

## Commands

All run from the repository root:

```console
$ uv sync                          # create/update the workspace environment
$ uv run pytest                    # run all tests
$ uv run pytest client/tests/test_cli.py::test_bare_invocation_shows_help
                                   # run a single test
$ uv run scripticus -v             # run the CLI
$ uv build --package scripticus    # build the client wheel/sdist into dist/
```

## Code layout

- Workspace root [pyproject.toml](pyproject.toml) is virtual (no `[project]`
  table) — it only declares workspace members. Add `server/` and `shared/`
  there as they come into existence.
- `client/` — the `scripticus` package (src layout, `uv_build` backend).
  CLI entry point is the Typer app in
  [cli.py](client/src/scripticus/cli.py); the console script maps
  `scripticus` to `scripticus.cli:app`. Tests live in `client/tests/` and
  use Typer's `CliRunner`.
- The version has a single source: `[project.version]` in
  [client/pyproject.toml](client/pyproject.toml), read at runtime via
  `importlib.metadata`.

## Documents and their roles

- [README.md](README.md) — user-facing description of the intended product
  (CLI usage, manifest format, server setup).
- [doc/VISION.md](doc/VISION.md) — two-paragraph purpose statement.
- [doc/ROADMAP.md](doc/ROADMAP.md) — v1.0.0 scope as a checklist, plus
  deliberately unscheduled post-v1 items.
- [doc/ARCHITECTURE.md](doc/ARCHITECTURE.md) — components, data flows, index
  data model.
- [doc/DECISIONS.md](doc/DECISIONS.md) — the decision record (D1–D24). Each
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
