# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Current state

This repository is **design-docs only — no code exists yet**. There are no
build, lint, or test commands. The docs describe the intended v1.0.0 of
Scripticus, a package manager and registry for scripts. When implementation
begins, it will be Python on both sides (CLI client, and a FastAPI index
service fronting Gitea), per decision D13.

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
