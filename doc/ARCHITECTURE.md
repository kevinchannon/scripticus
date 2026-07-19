# Architecture

This document describes the intended v1.0.0 architecture at the level of
components, responsibilities, data flows, and the shape of the index
service's data model. The read- and write-path API schemas are designed
(D30, D32) and live in `scripticus_schema.index_api` /
`scripticus_schema.publish_api`; the resolver algorithm's specifics are
deliberately not pinned down yet.

## Overview

```text
┌─────────────────┐         ┌──────────────────────┐        ┌─────────────┐
│  scripticus CLI  │ ──────► │  Index service        │ ─────► │  Gitea       │
│  (Python)        │  HTTP   │  (Python / FastAPI)   │  API   │  (storage,   │
│                  │ ◄────── │  search / resolve /   │ ◄───── │  auth,       │
│  shims, lockfile │         │  publish proxy        │        │  namespaces) │
└─────────────────┘         └──────────────────────┘        └─────────────┘
        │                                                          ▲
        └────────────────── blob download (direct) ────────────────┘
```

Three components:

1. **Gitea** — the substrate. Provides blob storage (generic package
   registry), authentication, and namespace ownership (users/orgs and their
   ACLs). Scripticus does not reimplement any of these. Gitea treats packages
   as opaque blobs; it has no knowledge of the manifest.
2. **Index service** — the Scripticus server. Owns everything
   manifest-aware: the package index, search, version resolution, dependency
   resolution, yank state, and the publish path. Python/FastAPI; Pydantic
   models double as the manifest schema shared with the client.
3. **CLI client** — Python. Owns the local machine: shims, install state,
   remotes/search-path configuration, scaffolding, and the interactive
   install flow.

Deployment target for the server side is a single `docker-compose.yml`
(index service + Gitea, SQLite-backed for small installations).

## Package identity

- Artifacts are **content-addressed**: the canonical identity of a package
  artifact is a hash over the package directory tree (Merkle-style, as git
  hashes trees). Name/version/variant are index metadata *pointing at*
  content, not the identity itself. This is what makes signing, provenance,
  and immutability straightforward to layer on later without changing
  reference formats.
- Names are **fully namespaced** (`owner/name`). Namespaces map 1:1 to Gitea
  users/orgs; allocation is first-come-first-served; publish permission is a
  Gitea ACL question. The `library` namespace is reserved.
- Bare-name usage (`scripticus install foo`) is purely a client-side
  resolution convenience over a configured, prioritised namespace search
  path. Nothing unnamespaced ever exists in storage or the index.
- Versions are strict semver, enforced at publish.
- One package version may have multiple **platform/language variant
  artifacts**. Artifact filenames carry wheel-style structured tags
  (name, version, platform, language; dashes within name/version normalised
  to underscores so the dash is an unambiguous separator), but filenames are
  human-legible redundancy only — the manifest, indexed at publish time, is
  the source of truth for resolution.

## Package format

A package is a directory, archived for transport (`.tar.gz` POSIX/macOS,
`.zip` Windows):

```text
<pkg>/
├── meta.toml           # manifest
├── LICENSE
├── README.md
├── src/                # scripts; main.<ext> if no [commands] table
└── test/
```

The TOML manifest declares: identity (namespace/name/version), language,
supported platforms (OS list, optional narrower distro list), system tool
dependencies (required/optional), package dependencies (semver ranges), and
optionally a `[commands]` table mapping command names to script paths.

Entrypoints: without `[commands]`, `src/main.<ext>` is the sole entrypoint
and the command name is the package name; with `[commands]`, every entry gets
a shim.

Scripticus performs **no correctness verification** of manifest claims
(platforms, tools) at any point. This is an explicit non-goal: it is
undecidable in general, and a partial check gives false confidence.
Correctness is the package author's responsibility.

## Write path (publish)

Publish is a **single atomic request** from the client's perspective:

1. Client validates the manifest locally (fail fast, UX only — not trusted).
2. Client sends archive + manifest to the index service.
3. Index service re-validates the manifest, enforces naming/semver rules,
   rejects duplicate versions, and checks publish permission by delegating to
   Gitea's ACLs.
4. Index service writes the blob to Gitea's generic package registry.
5. Only after Gitea confirms the write does the index service commit the
   index record.

Failure at any step rejects the whole publish. This removes the
orphaned-blob / dangling-index-entry class of inconsistency that a
client-writes-to-both design would allow.

Cycle detection for package dependencies happens at publish time.

## Read path (search / resolve / install)

- **Search** is served entirely by the index service (Gitea's generic
  registry has no usable programmatic listing/search; the index database is
  authoritative for discovery).
- **Resolution** is server-side. The client asks for a package (name, and
  optionally a version/range); the service resolves the version, then the
  full transitive dependency closure, and returns a flat list of
  (package, exact version, artifact pointer). Inputs to resolution include
  the client's platform, so the correct variant artifact is selected
  server-side.
- Resolution enforces **single-version-per-closure**: exactly one version of
  any package in a resolved set. Side-by-side versions are rejected as
  unsatisfiable rather than co-installed, because co-installation is
  incompatible with a shared shim/bin directory.
- **Blob download is direct**: the index service returns download
  pointers/tokens and the client fetches artifacts from Gitea itself,
  keeping the index service off the data path (npm-style split of metadata
  vs tarball fetch).
- The client verifies the content hash of every downloaded artifact against
  the resolved hash before installing.
- **Yank** (npm model) is index-service state: yanked versions are excluded
  from search and `latest`/range resolution but remain resolvable when pinned
  exactly (including via lockfiles). Artifacts are never hard-deleted.

## Index service data model

Relational, SQLite to start (via SQLAlchemy, avoiding SQLite-isms so a move
to Postgres remains a configuration change, not a rewrite — the same pattern
Gitea uses). The workload is read-heavy and low-write; publishes are rare
events.

```text
namespace          — mirrors a Gitea user/org; a cached reference/FK anchor
                     only. Gitea remains authoritative for ownership/ACLs.
  └── package      — (namespace, name), kebab-case; unique within namespace
        └── package_version   — (package, semver), immutable once written;
              │                 yanked flag; publish timestamp + publisher
              ├── artifact    — one per platform/language variant:
              │                 platform tags, language, archive format,
              │                 content (tree) hash, size, Gitea pointer
              ├── dependency  — target package + semver range constraint
              ├── tool_dep    — (name, required|optional)
              ├── command     — (command name → script path)
              └── manifest_blob — the verbatim manifest as published
```

Principles:

- **Verbatim manifest + extracted projection, with a fixed authority rule.**
  The manifest blob is the record (it is what the content hash covers, and
  the escape hatch for future fields the schema doesn't yet extract). The
  extracted tables exist to be queried (search, resolution, command
  listing). Extracted columns are a publish-time projection of the manifest:
  never independently editable, always re-derivable from the blob. This is
  the crates.io pattern, and it prevents "index says X, package says Y"
  drift.
- **Dependency graph as plain rows, resolved on demand.** At internal scale
  (hundreds to low thousands of packages), traversal of `dependency` rows —
  recursive queries or in-application — is instantaneous. No materialised
  transitive closures, no graph-database machinery, without a measured
  reason.
- **Yank is a flag, whole-version, for v1.** All rows for a yanked version
  remain; resolution filters `yanked = false` except for exact-pin lookups.
  Per-variant yank (yanking only, say, a broken Windows artifact) is a
  deliberate deferral: adding it later is a small, painless schema addition,
  whereas starting per-variant and simplifying back is not.
- **Own manifest-derived data; never cache anything ACL-shaped.** Gitea owns
  identity and permission truth. Publish re-checks namespace
  existence/publish rights against Gitea live rather than trusting a local
  row. The moment permissions live in two places, they disagree.
- **No install/download tracking.** Nothing in v1 needs the server to know
  who installed what — install state is client-side, and downloads bypass
  the index service entirely (see read path above). Usage metrics, if ever
  wanted, are a new design concern that interacts with the direct-download
  decision, not a gap here.

## Client-side state

Everything lives under `~/.scripticus/`:

- **`config.toml`** — the remotes list (prioritised; doubles as the
  bare-name namespace search path) and defaults. Distributable org-wide via
  `scripticus config install <git-url>` (Conan-style). No Conan-style
  profiles.
- **`installed.lock`** — install state: every installed package with exact
  version and content hash, the full resolved closure with
  direct-vs-transitive marking, and provenance (remote vs local `-f`
  install). `update` and `uninstall` operate against this without a server
  round-trip; `update` skips local-provenance entries with a warning.
- **`bin/`** — the shim directory, added to PATH once at client install
  time. POSIX shims are symlinks or one-line wrappers; Windows shims are
  generated `.cmd` files invoking the correct interpreter (interpreter choice
  comes from the manifest's language field; no compiled shims needed since
  targets are scripts, not binaries with DLL dependencies).

### Install transaction semantics

Install fully resolves before presenting anything, then shows a dnf-style
transaction summary: actions only (new installs and version changes, with
downgrades called out; already-satisfied dependencies are not listed),
followed by shim conflicts presented distinctly and naming the namespaced
current owner of each affected shim.

- Interactive: accept the whole transaction or abort. No per-item selection
  (it reintroduces partial-install ambiguity).
- `-y`/`--yes` = `--force=no-conflicts`: auto-accept, but any shim conflict
  aborts the entire transaction (nothing installed, non-zero exit).
- `--force=all`: auto-accept including overwrites; every overwritten shim is
  reported.
- Collisions otherwise: last-install-wins, `scripticus use` re-points a shim
  manually, and namespaced invocation is always available.

## Forward compatibility (public offering)

Decisions above were made so that widening beyond one organisation is
additive, not a rework:

- Content-addressing enables signing/verification (Sigstore/cosign-style)
  and provenance (SLSA-style) as layers on the existing identity scheme.
- Publish review gates are a policy change on the existing single publish
  path.
- Multi-tenant/public hosting is a client-configuration change (different
  default remotes/search path), not an identity or storage change.
- The reserved `library` namespace is the future home of curated packages.

Deliberately not designed yet: auth token scoping for CI publishing and
the resolver algorithm's internals. The read- and write-path API schemas
are designed (D30, D32; publish auth is pass-through of the caller's
Gitea token, D32).
