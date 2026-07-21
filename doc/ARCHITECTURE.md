# Architecture

This document describes the intended v1.0.0 architecture at the level of
components, responsibilities, data flows, and the shape of the index
service's data model. The read- and write-path API schemas are designed
(D30, D32) and live in `scripticus_schema.index_api` /
`scripticus_schema.publish_api`; the resolution architecture is designed
(D42/D43), with only the solver's algorithmic internals left to
implementation.

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
   remotes/search-path configuration, publish credentials (D34),
   scaffolding, and the interactive install flow.

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

### Version specifications

A dependency spec — the string value of a `[dependencies.packages]` entry
and the `@<spec>` in `install pkg@<spec>` — is an npm/cargo-style range,
parsed by `scripticus_schema.version_spec`. The grammar:

- **Caret** `^1.2.3`: `>=1.2.3` and `<2.0.0`. The upper bound comes from
  the left-most non-zero component, so `^0.2.3` is `<0.3.0` and `^0.0.3`
  is `<0.0.4`. The operand may be partial: `^1`, `^1.2`.
- **Comparators** `>= > <= < =`, with a comma as AND (intersection), e.g.
  `>=1.2, <2.0.0`. Partial operands pad with zeros (`>=1.2` is `>=1.2.0`).
- **Bare full version** `1.4.3`: an exact match (equivalent to `=1.4.3`).
  A bare *partial* (`1.2`) is rejected as ambiguous — npm reads it as
  `1.2.x`, cargo as `^1.2` — so the author must write `^1.2` or `1.2.0`.
- **`*`** (or the empty string): any released version.

Prereleases are opt-in: a prerelease version (`1.0.0-rc1`) is selected
only by an exact pin equal to it; caret and comparator ranges never match
a prerelease candidate, so a plain `install foo` never surprises anyone
with a release candidate. This is stricter than npm's same-core rule — a
deliberate simplification a future need could revisit.

The module also exposes `select_version(specs, candidates)` — the highest
candidate satisfying every spec, or none for an empty window. This is the
reusable **version-window primitive** the resolver runs for packages
(against the index) and tools (against the local package manager, D42/D43).

## Write path (publish)

Publish is a **single atomic request** from the client's perspective:

1. Client validates the manifest locally (fail fast, UX only — not trusted).
2. Client sends one or more archives — a version's whole format-group set,
   D37 — to the index service in a single request.
3. Index service re-validates every archive's manifest, enforces
   naming/semver rules, rejects duplicate versions, and checks publish
   permission by delegating to Gitea's ACLs.
4. Only once every archive in the batch has validated does the index
   service write each blob to Gitea's generic package registry.
5. Only after Gitea confirms every write does the index service commit the
   index record(s).

Failure at any step rejects the whole publish. This removes the
orphaned-blob / dangling-index-entry class of inconsistency that a
client-writes-to-both design would allow.

Declared package dependencies must be fully namespaced and already
present in the index, and cycle detection happens at publish time (D33).

A multi-format package (D26) publishes as a **batch**: `POST /packages`
accepts a multipart request carrying one or more archives, validates
every one of them, and only writes any blob to Gitea (and commits any
index record) once the whole batch has validated — atomic across the
batch, not just per archive (D37). A failure anywhere in the batch
rejects the whole request; nothing is uploaded, nothing is committed.
Client-side, `scripticus publish <path-prefix>` (D36) selects every
pre-built archive matching a `<name>-<version>` prefix and sends them
all in one request; the command reports the whole set published or the
whole set rejected, with no partial-success state to reason about. The
format-variant rule (same content hash, format not yet present) still
governs a genuinely *separate* publish later — e.g. adding a Windows
build to an already-published Linux/macOS version next week — which is
a batch of one, validated against already-committed index state.

## Read path (search / resolve / install)

- **Search** is served entirely by the index service (Gitea's generic
  registry has no usable programmatic listing/search; the index database is
  authoritative for discovery).
- **Resolution** is a server-side solver fed the client's state (D42).
  `POST /resolve` takes the root package (name, optional version/range),
  the client's platform, and the client's installed closure as
  **identities only** — each installed package as
  `namespace/name@version`, no constraints; the server re-derives each
  installed version's constraints from its own index (D21), which it can
  because D33 keeps every dependency edge within one index. So the request
  scales with the installed count alone and the response stays bounded by
  the root's closure, not the installed set. The service walks the
  dependency graph (acyclic by D33), consolidates each package to a single
  node, and picks the highest
  version satisfying the intersection of every constraint reaching it,
  with the installed packages entered as **hard constraints** — so a
  resolve neither breaks an already-installed package nor needlessly bumps
  one that still satisfies. It returns a flat closure of (package, exact
  version, content hash, Gitea pointer, direct/transitive) plus the
  aggregated tool requirements. Platform is an input, so the correct
  variant artifact is chosen server-side. The intersect-and-pick-highest
  step is a reusable **version-window primitive**, shared with tool
  resolution.
- Resolution enforces **single-version-per-closure**: exactly one version of
  any package in a resolved set. Side-by-side versions are rejected as
  unsatisfiable (a hard error naming the conflict) rather than
  co-installed, because co-installation is incompatible with a shared
  shim/bin directory (D11). Version-qualified shims to relax this are a
  post-v1 option (D42), with D38's fully-qualified tier as the hook.
- **Tool requirements** resolve across the boundary (D43): the server
  aggregates each tool's requirement over the closure; the client checks
  them against the local machine and installs the missing set by shelling
  out to an operator-configured command (D44 — see Tool installation
  below), before installing any package. v1 is name-only (PATH presence);
  versioned tool windows are a fast-follow needing a manifest/schema
  extension.
- **Blob download is direct**: `resolve` returns Gitea download pointers
  and the client fetches artifacts from Gitea itself with its stored token
  (D9) — no companion download endpoint — keeping the index service off
  the data path (npm-style split of metadata vs tarball fetch).
- The client verifies the content hash of every downloaded artifact against
  the resolved hash before installing. Downloads **stage-then-commit** —
  every blob fetched and verified before any unpack/shim — mirroring
  publish atomicity so a mid-fetch failure never leaves a partial install
  (D17). System-tool installs are the exception: benign additions the PM
  owns, not rolled back on a later failure.
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
  |                  only. Gitea remains authoritative for ownership/ACLs.
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

- **`config.toml`** — the remotes list as an ordered `[[remotes]]` array of
  `{ name, url }` tables (D35); array order is both search-path priority
  (doubling as the bare-name namespace search path, D5) and `publish`'s
  default target, plus other defaults. Optionally a `[tools]` table whose
  `install` command Scripticus shells out to for system-tool installation
  (D44). Distributable org-wide via `scripticus config install <git-url>`
  (Conan-style). No Conan-style profiles.
- **`credentials.toml`** — Gitea personal access tokens, one per remote,
  stored plaintext with 0600 permissions (cargo-style, D34), keyed by
  remote URL. Registered via `scripticus login <name>` (resolving an
  already-configured remote) or `scripticus login <name> <url>` (also
  registering the remote in `config.toml` on first use, D35), and replayed
  as the `Authorization` header on publish (D32). `SCRIPTICUS_TOKEN`
  overrides it in CI. A separate file from `config.toml` deliberately, so
  org-distributed config (D12) can never carry a token.
- **`installed.lock`** — install state: every installed package with exact
  version and content hash, the full resolved closure with
  direct-vs-transitive marking, and provenance (remote vs local `-f`
  install). `update` and `uninstall` operate against this without a server
  round-trip; `update` skips local-provenance entries with a warning.
- **`bin/`** — the shim directory, added to PATH once at client install
  time by `scripticus init` (D39). POSIX shims are symlinks or one-line
  wrappers; Windows shims are
  generated `.cmd` files invoking the correct interpreter (interpreter choice
  comes from the manifest's language field; no compiled shims needed since
  targets are scripts, not binaries with DLL dependencies). Each command
  materialises three shims (D38): a guaranteed-unique
  `<namespace>.<package>.<command>`, a `<namespace>.<command>` convenience,
  and the bare command name. Convenience shims point directly at the
  fully-qualified shim, so any shim reveals its true owner in one hop, and
  a shim name's dot count identifies its tier (the identifier character
  sets all exclude `.`).

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
- Collisions otherwise: last-install-wins at both convenience tiers (bare
  and `<namespace>.<command>`), with `scripticus use` re-pointing either
  manually; the fully-qualified `<namespace>.<package>.<command>` shim never
  collides, so every installed command stays invocable (D38 — this is why
  no `run` command exists).

### Tool installation

The closure's aggregated system tools (D43) are handled during apply by
shelling out to an operator-configured command — Scripticus encodes no
package-manager logic (D44). Flow:

- Each required tool is checked for **presence on `PATH`**; the missing set
  is the install list. (An install-only command cannot be queried for
  available versions, so v1 "satisfiability" is presence, not a version
  window — versioned windows and an optional query/check command are
  post-v1.)
- If `config.toml` has `[tools] install`, the missing set is substituted
  into it — a `{packages}` placeholder (shell-quoted names, space-joined;
  appended if the placeholder is absent) — and it runs once through the
  platform shell (`bash -lc` / `cmd /c`) inheriting the process
  environment. The shell expands env vars, so proxies, mirrors, and
  credentials come from the machine environment and never sit in the
  (org-distributable) config.

```toml
[tools]
install = "apt-get install -y {packages}"   # no sudo — see below
```

- **No privilege management.** The command carries no `sudo`/`doas`; if
  installing tools needs root, the whole `scripticus install` is run as
  root. Because package files and shims are user-space (`~/.scripticus`),
  the natural pattern is to provision tools once as root (or pre-install
  them) and install packages as yourself — running the whole install as
  root otherwise writes user state under root's home unless
  `SCRIPTICUS_HOME`/`sudo -E` is set.
- **No installer configured** → Scripticus never invokes a package
  manager: missing *required* tools abort the install listing them (with a
  `--skip-tools` escape), missing *optional* tools are only reported.
- Tool names originate in third-party manifests, so they are validated to
  a safe charset (`[A-Za-z0-9][A-Za-z0-9._+-]*`) at manifest parse and
  shell-quoted at invocation: a manifest cannot inject shell.

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

Deliberately not designed yet: auth token scoping for CI publishing. The
resolution architecture is now designed (D42/D43 — a server-side solver
fed the client's installed state, resolve-then-fetch, tool resolution
split across the boundary); only the solver's algorithmic internals
(backtracking specifics) remain implementation detail rather than
contract. The read- and write-path API schemas are designed (D30, D32;
publish auth is pass-through of the caller's
Gitea token, D32, obtained via `scripticus login` and stored per named
remote, D34/D35). Token verification at login is served by the index
service's `GET /whoami`, a pass-through of the caller's Gitea token to
Gitea's `/user` returning the authenticated login (D40); `scripticus
login` calls it to verify a token before storing it, reporting the
authenticated identity and refusing to store an unverified one (D41) —
together completing D34's deferred verification.
