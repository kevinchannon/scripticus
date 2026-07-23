# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Current state

The write path is implemented end-to-end (pack → login → publish →
index + Gitea), and the remote read path is now complete on both halves:
the server-side resolver (`POST /resolve`, D42/D43) and the client's
resolve-then-fetch remote `install` (D42/D46), plus discovery — two verbs
fanning out across every configured remote and merging the hits (D48/D49):
`search <text>` matching package *content* (name, description, command
names) and `list [glob]` enumerating *identity* dnf-style (Installed +
Available sections). The v1 client surface is now feature-complete, and the
first post-v1 command has landed: `update [<pkg>...]` (D52/D53 — `update.py`),
which re-resolves installed remote packages against their remotes, reusing
install's plan/stage/apply back half but sending its targets as roots with
`upgrade=True` so they float to the newest compatible version while everything
else stays put; a target a shared constraint holds back is reported with the
blocker named (the server's held-back diagnostic), a shrunk command set has its
orphaned convenience shims reconciled through the D28 uninstall picker, and a
system tool the closure no longer needs produces an advisory (never an
uninstall, D44). The last write-path command has also landed: `yank
<ns/name>@<ver>` (D54 — `yank.py`), which flips a version's whole-version
`yanked` flag (read-side already enforced everywhere) via a `PATCH` on the
version, owner-authed with publish's live Gitea ACL and touching no blob;
`yank --undo` reverses it with no time window, since yank deletes nothing.
`config install` remains. The repo is a **uv workspace**
(Cargo-style) with four members: `client/` (PyPI package `scripticus`,
the CLI), `server/` (PyPI package `scripticus-server`, the FastAPI index
service fronting Gitea, providing the `scripticus-svr` command),
`schema/` (PyPI package `scripticus-schema`, the shared client/server
contract — the declarative wire and manifest shapes, D29), and `common/`
(PyPI package `scripticus-common`, the pure deterministic helpers both
sides must compute identically — hashing, versioning, identity globbing,
D51). The client is a Typer + Rich CLI. It implements `-v`/`--version`, `new`
(scaffolding, `scaffold.py`), `pack` (archive creation, `pack.py`),
`install <ns/name>[@spec]` (remote install: resolve against the configured
remotes in priority order — first hosting the root, `--remote` to force —
POSTing the installed closure to `/resolve`, planning via the D17
transaction flow, installing tools first (D43/D44), then staging + tree-hash
verifying every blob before any unpack — `remote_install.py`, D42/D46;
fully-namespaced only for v1), `install -f` (local install: extraction,
transaction flow, three-tier shims, lockfile — `install.py`, with the
per-package apply shared with remote install via `install_into_lock`; every
command gets a guaranteed-unique `<ns>.<pkg>.<cmd>` shim plus `<ns>.<cmd>`
and bare convenience shims that delegate to it, D38, with convenience-shim
ownership tracked in each lockfile entry's `shims` list), `uninstall`
(lockfile-driven removal of a package's files and owned shims, with a
replacement picker for convenience shims other installed packages still
provide, D28 — `uninstall.py`), `use` (manually re-point a convenience shim
by name at an installed package, D11/D38 — `use.py`, sharing the uninstall
picker's re-point primitive), `login` (token capture per remote, doubling as first-time
remote registration, D34/D35 — decision logic in `login.py`, the
`[[remotes]]` and optional `[tools]` config in `config.py`, the 0600
URL-keyed credential store in `credentials.py`; the token is verified
against the remote's `/whoami` before storing, D41 — `whoami.py`), and
`publish` (D36/D37 — `publish.py`: structural
name-version matching of pre-built archives, one batched multipart POST
to the first-listed or `--remote`-named remote, token via
`SCRIPTICUS_TOKEN` or the credential store, 401 mapped to an actionable
re-login message), `search` (D48/D49 — `search.py`: query every configured
remote's `GET /search` in priority order and merge the hits, each tagged
with its remote; content match over name/description/command names,
`--remote` to restrict, `--platform`/`--language` filters, best-effort so a
down remote is a warning not a failure, anonymous read), `list` (D49 —
`listing.py`: dnf-style identity enumeration with a shell glob over
`namespace/name` — an Installed section from the lockfile and an Available
section from the remotes' `GET /packages` catalog minus what's installed,
`--installed` (offline) / `--available` to restrict; the glob runs
server-side via the shared `scripticus_common.identity_glob` primitive so the
installed and available halves match identically, D50), and `init` (post-install PATH bootstrap, D39 —
`init.py`). The shared code is split by function (D51). `schema/`
(`scripticus_schema`) holds the **declarative shapes**: the Pydantic manifest
model and validation (`manifest.py`, whose version field reuses `common`'s
semver grammar), plus the wire models for the read API (`index_api.py`, D30),
resolution (`resolve_api.py`, D42/D43/D52 — the request carries a list of
roots, the installed closure as identities, and an `upgrade` flag; the response
the resolved closure with each package's command map and any `held_back`
diagnostic, D47/D52), publish response (`publish_api.py`, D32), and token
verification (`whoami_api.py`, D40). `common/` (`scripticus_common`) holds the
**pure deterministic computations** both sides must compute identically: the
D3/D27 content hash (`treehash.py`), semver ordering (`semver.py`), the
version-spec grammar plus the reusable version-window primitive
(`version_spec.py`; grammar documented in ARCHITECTURE.md, primitive serving
D42/D43), and the `namespace/name` glob primitive (`identity_glob.py`, D50 —
one `fnmatch` rule both sides of `list` use). The manifest's tool-name charset
validation lives with the manifest in `schema/` (D44). Each package's charter
is its admission rule: `schema` admits a declarative shape (defines what a
package is or how client and server communicate, D29); `common` admits a pure,
deterministic function that must give identical results on both ends (D51).
Client-side state goes under `~/.scripticus/`
(override with `SCRIPTICUS_HOME`, which tests rely on). The server is a
FastAPI app (`app.py`) exposing `GET /health`, `GET /version`,
`GET /whoami` (pass-through Gitea token verification, D40), and the
read endpoints — `GET /packages/{namespace}/{name}` (version listing),
`GET /search` (case-insensitive content match over name/description/command
names plus `platform`/`language` filters, D49), `GET /packages` (identity
listing: a `namespace/name` glob via the shared `fnmatch` primitive, D50),
and `POST /resolve` (the D42/D43/D52 resolver — `resolve.py`:
a backtracking solver over an `Index` abstraction, fed a list of roots and the
client's installed closure as identities and hard constraints, single-version-
per-closure, aggregating tool requirements name-only for v1 and returning each
package's command map, D47; with `upgrade` set the roots float off their
installed-version preference and a post-solve probe fills each held-back root's
`held_back` diagnostic, D52) — backed by the
SQLAlchemy index data model (`db.py`,
D23; tables created via `create_all` on first use, D31; DB URL from
`SCRIPTICUS_INDEX_DB`, default a local SQLite file), plus the write path:
`POST /packages` (`publish.py`, D32/D37 — pass-through Gitea auth against
`SCRIPTICUS_GITEA_URL`, everything derived from the uploaded archives,
the whole batch validated before any blob goes to Gitea and every blob
confirmed before the index record commits; dependency rules per D33),
and `PATCH /packages/{namespace}/{name}/{version}` (`yank.py`, D54 — flips a
version's whole-version `yanked` flag from a `{"yanked": bool}` body, reusing
publish's `authenticated_user`/`can_publish` ACL, no Gitea blob touched, 404
on an unknown version; the client's `--undo` sends `false`),
with the Gitea boundary isolated in `gitea.py` so tests fake it
(the full-stack end-to-end suite is instead BATS-driven and lives in `tests/`
— D55: the Tasktree `e2e-test` task builds the client wheels and runs
`tests/e2e.sh` in a containerised docker-out-of-docker runner, which stands the
whole registry bundle up from source via a `docker-compose.e2e.yml` overlay,
joins its network, and drives the real client through the README's lifecycle;
`tt build`/`unit-test` round out the task graph, run by
`.github/workflows/e2e.yml`). The server has
no Typer CLI — `scripticus-svr` (`main.py`, argparse for
`--host`/`--port`) prints a version/address banner and runs uvicorn, and
the OpenAPI spec is served at `/openapi.json` rather than committed to
the repo. System-tool installation shells out to an operator-configured
command (`tools.py`, D44 — the `[tools] install`/`escalate` config, PATH
presence check, `{packages}` substitution, platform-shell run; refuses when
a required tool is missing and no installer is configured, with a
`--skip-tools` escape). With `search` and `yank` in, every v1 client command
is implemented; only the post-v1 `config install` remains. A
server `Dockerfile` exists, and the root `docker-compose.yml` is the
registry bundle: a Caddy reverse-proxy front (`proxy/Caddyfile`, D45)
presenting one user-facing URL over the index service and Gitea. The design
docs below describe the intended v1.0.0 and remain the source of truth for
architecture.

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

**Prefer the automated path.** The whole runbook below is encoded in the Task
Tree recipe [tasktree.yaml](tasktree.yaml) — when asked to cut a release, use
`tt release` rather than performing the steps by hand. Pass a bump per package
(`none|patch|minor|major`), e.g. `tt release schema=minor server=minor
client=patch`; it derives each next version from the git tags, rewrites the
internal pin windows, commits and pushes them, then pushes the release tags
tier by tier (common → schema → server/client), waiting after each tier for the
workflow to go green and the package(s) to appear on PyPI before the next. Add
`dry_run=yes` to preview the plan without touching anything (always do this
first if unsure), and `tt versions` lists the current released versions. The
manual runbook below is what `tt release` automates — the source of truth for
its behaviour and the fallback if `tt` is unavailable.

Releases are tag-driven, one tag per package: pushing `client-vX.Y.Z`
releases `scripticus` to PyPI, `schema-vX.Y.Z` releases `scripticus-schema`,
`common-vX.Y.Z` releases `scripticus-common`
(`.github/workflows/release.yml` — one independent run per pushed tag, so
tagging several packages at once is fine). The tag's version is stamped
into the member's `pyproject.toml` at build time. A release waits for a PyPI
release satisfying every internal pin before publishing (D29/D51): `common`
pins nothing, `schema` pins `common`, and `client`/`server` pin both — so the
publish order is common → schema → client/server, which the workflow enforces
by polling each pin's installability. The pipx-install validation runs only
for `client-v*` releases. A `server-v*` tag additionally pushes a Docker image to
`kevinchannon/scripticus-server` (version + `latest` tags) after the PyPI
publish succeeds; this needs the `DOCKERHUB_USERNAME`/`DOCKERHUB_TOKEN`
repo secrets.

Version numbers live only in tags (the checked-in `version` is always the
`0.0.0.dev0` placeholder), but the internal **dependency pins do live in the
committed `pyproject.toml`s** and must be bumped *before* tagging, since each
release builds from its tag's commit. The pins use tight same-minor windows
(`>=X.Y,<X.(Y+1)`), so pre-1.0 a **minor** bump is the compatibility boundary:
a breaking change to a shared package (e.g. a wire-shape change in `schema`)
must cross a minor and each consumer must move its pin window to match, or the
consumer would silently accept an incompatible release.

**Release runbook** (only tag the packages that actually changed since their
last tag — `git diff --name-only <pkg>-vLAST..HEAD -- <pkg>/src`):

1. **Bump the pins in one commit, up front.** If a shared package's minor is
   moving, edit every consumer's pin window to the new minor (e.g. bumping
   `schema` to `0.2.0` means `client`/`server` move to
   `scripticus-schema>=0.2.0,<0.3.0`). A shared package whose minor is *not*
   moving needs no consumer edit. Push this commit to `main`.
2. **Push tags in dependency order, waiting for each to land on PyPI before
   the next tier:** common → schema → client/server. The release job polls for
   its pins for only ~5 minutes (30×10s), so a dependent pushed too early — or
   a slow PyPI — can time the job out; pushing a tier only once its
   dependencies are visible on PyPI keeps the poll a formality rather than a
   race. Tags within a tier (e.g. `server` and `client`) can go together.

So for a change touching schema + server + client but not common: commit the
`client`/`server` schema-pin bump → push `schema-vX.Y.0`, wait for it on PyPI →
push `server-vX.Y.0` and `client-vX.Y.0` together.

## Code layout

- Workspace root [pyproject.toml](pyproject.toml) is virtual (no `[project]`
  table) — it declares workspace members and shared pytest config (importlib
  import mode, so same-named test modules in different members coexist).
- `schema/` and `common/` are the shared members, each declared by its
  consumers as a normal PyPI dependency with tight same-minor bounds plus a
  `[tool.uv.sources]` workspace source (D29/D51). `common` depends on nothing;
  `schema` depends on `common` (the manifest reuses its semver grammar);
  `client`/`server` depend on both. Built wheels do not vendor workspace
  members, so a shared package must be published to PyPI before any dependent
  release that bumps its pin (common before schema before client/server).
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
- [doc/DECISIONS.md](doc/DECISIONS.md) — the decision record. Each
  entry has decision, reasoning, and consequences (good *and* bad). Entries
  stay terse — match the register of D1–D11 (a tight Decision paragraph, a
  tight Reason, short consequence bullets). Architectural elaboration of a
  decision's mechanics belongs in ARCHITECTURE.md, not the record.

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
- **Single shared bin dir, three-tier last-install-wins shims** (D11/D38):
  each command has a guaranteed-unique `<ns>.<pkg>.<cmd>` shim plus `<ns>.<cmd>`
  and bare convenience shims; only the convenience tiers collide, under the
  dnf-style transaction flow (D17) and split `--force` semantics (D18). Exit
  codes never mean "partially installed".
- **SQLite via SQLAlchemy with no SQLite-isms** (D23), so Postgres stays a
  configuration change.

Deliberately not designed yet (per ARCHITECTURE.md): auth token scoping
for CI publishing. Resolution is now designed (D42/D43); only the
solver's algorithmic internals remain implementation detail. The read-
and write-path API schemas are designed (D30, D32).
