# Decision Record

Format: each decision has a short description, the reasoning, and
consequences (good and bad). Numbered in roughly the order they were made.

---

## D1. Internal-first, public-capable

**Decision**: Build for single-organisation internal use first; avoid
decisions that would preclude adding security guarantees required for a
public offering later.

**Reason**: The internal use case is clearly valuable and lets us get the
core right. Public distribution of runnable scripts carries a trust problem
(the same one npm/PyPI have) that we don't need to solve on day one — but
retrofitting identity/integrity foundations is expensive, so those are done
now.

**Consequences**:
- Good: v1 trust model reduces to "who's allowed to publish", solved by org
  ACLs; no account system, reputation, or signing infrastructure needed.
- Good: forces early identification of the genuinely hard-to-retrofit
  decisions (D3, D4) vs deferrable ones (signing, review gates).
- Bad: some v1 effort (content-addressing rigour) pays off only if the
  public version happens.

---

## D2. Build on an existing registry substrate (Gitea), not from scratch

**Decision**: Use Gitea's generic package registry as the storage, auth, and
namespace-ownership substrate. Scripticus is a thin index service + CLI on
top. Chosen over Nexus raw, and over writing our own storage layer.

**Reason**: Storage, auth, org/team ACLs, and a web UI are commodity;
Scripticus's value is the script-specific layer (manifest-aware search,
resolution, shims, UX). Gitea beats Nexus on ops simplicity (single Go
binary, SQLite option, one Docker image vs JVM + partially non-OSS formats),
which serves the "trivial to stand up" goal. Gitea's known gaps — no
manifest awareness and no programmatic package listing/search for generic
packages — are absorbed by the index service we were building anyway.

**Consequences**:
- Good: auth, namespace ownership, and blob storage never have to be built
  or maintained by us.
- Good: single `docker-compose.yml` deployment.
- Bad: the index database is authoritative for discovery, so index↔Gitea
  consistency is our problem (mitigated by D8).
- Bad: coupled to Gitea's API surface; a substrate swap later would touch
  the index service's storage adapter.

---

## D3. Content-addressed artifact identity

**Decision**: From day one, an artifact's canonical identity is a hash of
the package directory tree (Merkle-style). Name/version/variant are index
metadata pointing at content.

**Reason**: This is the one thing that's genuinely painful to retrofit.
With it in place, signing, provenance, immutability, and integrity
verification all become additive layers that don't change how anything is
referenced.

**Consequences**:
- Good: install-time integrity verification for free; future
  Sigstore/SLSA-style layers need no reference-format changes.
- Good: multi-file packages get a stable identity regardless of file count.
- Bad: slightly more implementation work up front than name+version keys.

---

## D4. Fully namespaced packages; no flat tier; `library` reserved

**Decision**: All packages are `owner/name` (GitHub-style). No global flat
namespace at any tier. The `library` namespace is reserved for a future
curated/reviewed programme. Namespaces map 1:1 to Gitea users/orgs,
first-come-first-served, with publish rights following Gitea ACLs.

**Reason**: Flat namespaces caused squatting/collision problems for
crates.io and forced PEP 503-style normalisation retrofits on PyPI. Scoped
names cost nothing internally and eliminate the problem class entirely.
Mapping ownership onto Gitea makes namespace enforcement Gitea's ACL
problem, not ours.

**Consequences**:
- Good: no squatting, no collision policy, no name-dispute process needed.
- Good: zero namespace-enforcement code in the index service.
- Bad: an owner concept must exist before anything can be published
  (slightly more friction than flat first-publish).

---

## D5. Bare-name install via a configurable namespace search path

**Decision**: `scripticus install <pkg>` without a namespace resolves
against a per-user/org prioritised namespace list (Homebrew-tap-style),
configured client-side. Chosen over a Docker Hub-style single reserved
default namespace.

**Reason**: Gives the desired short-command ergonomics without any
unnamespaced artifact ever existing. Avoids reintroducing scarcity/squatting
within a privileged tier, and composes with internal→public: internally the
company namespace is everyone's default; publicly each user configures their
own.

**Consequences**:
- Good: ergonomics without weakening D4; identity is unaffected.
- Good: org-wide defaults distributable via config (D12).
- Bad: the same bare command can resolve differently on differently-
  configured machines — mitigated by the lockfile recording full identities.

---

## D6. TOML manifest declaring platforms, language, tools, deps, commands

**Decision**: Each package carries a TOML manifest (originally
`scripticus.toml`; renamed to `meta.toml` by D25)
declaring identity, language, supported platforms (OS + optional distro
narrowing), required/optional system tools, package dependencies, and
optionally a `[commands]` table.

**Reason**: "Will this run here" can't be determined from script content;
every comparable system (Nix, Homebrew, Ansible Galaxy) has an equivalent.
TOML over YAML for the usual indentation-footgun reasons and ecosystem
precedent (Cargo).

**Consequences**:
- Good: enables platform-aware resolution, tool-presence checks at install,
  and manifest-aware search.
- Bad: declarations are self-reported and unverified (see D14).

---

## D7. Package = directory, archived per-platform

**Decision**: The package unit is a directory (manifest, `src/`, `test/`,
LICENSE, README), archived for transport: `.tar.gz` on POSIX/macOS, `.zip`
on Windows. Multiple platform/language variants of one version may coexist,
with wheel-style structured filename tags (dashes in name/version normalised
to underscores in filenames). Filenames are human-legible redundancy; the
manifest is the source of truth for resolution.

**Reason**: Multi-file scripts are a requirement. Python wheels are the
proven model for coexisting platform variants of one version with automatic
client-side selection; copying it avoids inventing a parsing scheme, and the
underscore normalisation is what keeps dash-separated fields parseable when
names themselves contain dashes.

**Consequences**:
- Good: solved-problem semantics; variant selection can be automatic.
- Good: format familiarity for anyone who knows wheels.
- Bad: two archive formats to produce and test rather than one.

---

## D8. Atomic server-mediated publish

**Decision**: The client publishes with a single request to the index
service; the service validates, writes the blob to Gitea, and commits the
index record only after Gitea confirms. Chosen over the client writing to
Gitea and the index separately with background reconciliation.

**Reason**: Two client-side writes create orphaned-blob / dangling-index
states that are miserable to debug once real users hit them. A single
server-side write path makes publish atomic from the user's perspective and
gives one natural enforcement point for validation and ACL checks.

**Consequences**:
- Good: eliminates an entire inconsistency class; publish is all-or-nothing.
- Good: server-side manifest validation can't be bypassed by a client.
- Bad: publish traffic flows through the index service (acceptable: publish
  is low-volume; the read path stays direct per D9).

---

## D9. Direct blob download on the read path

**Decision**: Search/resolve return metadata plus download pointers/tokens;
the client fetches artifacts directly from Gitea.

**Reason**: Keeps the index service off the data path (npm's registry/
tarball split). No current requirement for per-download interception.

**Consequences**:
- Good: index service load stays proportional to metadata operations.
- Bad: no central download metrics/logging without revisiting this.

---

## D10. Client config: remotes + lockfile-style install state; no profiles

**Decision**: Client state is a remotes list (doubling as the D5 search
path) and a lockfile-style install-state file (exact versions, hashes, full
resolved closure with direct-vs-transitive marking, provenance). Conan-style
profiles were considered and rejected.

**Reason**: Profiles solve per-target build variation, which scripts don't
have; their complexity isn't earned here. Install state is required for
`update`/`uninstall` to work without server round-trips and for local-file
provenance handling.

**Consequences**:
- Good: minimal config surface.
- Bad: if a per-target-install scenario (provisioning another machine)
  becomes real, something profile-shaped may return.

---

## D11. Shims: one bin dir on PATH; last-wins conflicts with explicit `use`

> **Extended by [D38](#d38-three-tier-command-shims-a-guaranteed-unique-tier-convenience-tiers-and-no-run-command).**
> The shim scheme becomes three tiers per command — a guaranteed-unique
> `<namespace>.<package>.<command>` shim plus `<namespace>.<command>` and
> bare conveniences — and this entry's "namespaced invocation always
> available" is realised structurally by the fully-qualified tier rather
> than by a `run` command, which D38 drops. The single bin dir, last-wins
> rule, and `use` are unchanged; they now apply uniformly to both
> convenience tiers.

**Decision**: A single `~/.scripticus/bin` on PATH, populated with
per-command shims (POSIX: symlink/one-line wrapper; Windows: generated
`.cmd` — no compiled ShimGen-style shims). Command-name collisions:
last-install-wins, `use` to re-point manually, namespaced invocation always
available.

**Reason**: Chocolatey's single-bin-dir insight without its Windows-binary
machinery, which exists for DLL/icon problems scripts don't have. Last-wins
matches Homebrew/Chocolatey precedent and stays predictable; the interactive
conflict surfacing (D17) covers the safety side.

**Consequences**:
- Good: PATH is mutated once, ever; installs never touch it.
- Good: trivially inspectable (`ls` the bin dir).
- Bad: last-wins means installs can change existing behaviour — accepted,
  and mitigated by D17/D18 confirmation and conflict gating.

---

## D12. Org-distributable client config (`config install <git-url>`)

**Decision**: Adopt Conan's `config install` pattern for rolling out
remotes/defaults org-wide from a shared repo.

**Reason**: Internal-first means onboarding many machines to the same
registry and search path; one command beats documented manual steps.

**Consequences**:
- Good: onboarding is one command; config drift is reduced.
- Bad: a shared config repo becomes something someone must own and secure.

---

## D13. Python on both client and server

**Decision**: Client and index service both in Python (server: FastAPI).
Go and Rust were considered for the server.

**Reason**: The workload is I/O-bound; performance doesn't discriminate.
The deciding factor is the manifest schema needing to exist on both sides
(client validates for UX, server validates authoritatively): one shared
Pydantic model beats maintaining the same schema in two languages, which is
exactly the kind of thing that drifts. Go's single-binary deployment and
Gitea-ecosystem alignment were the counterarguments; FastAPI's
Pydantic-native validation and free OpenAPI docs sealed it.

**Consequences**:
- Good: one schema definition, shared client/server; no drift.
- Good: OpenAPI spec for free (the CLI is just another API consumer).
- Bad: server container needs a Python runtime rather than a static binary.

---

## D14. No framework-level manifest correctness checks — at all

**Decision**: Scripticus performs no verification that declared platforms/
tools match reality: no sandboxed execution, no static analysis, and no
advisory lint pass either. Correctness is entirely the author's
responsibility, stated explicitly in docs.

**Reason**: Full verification doesn't generalise (containers can't
represent hardware/GUI/privilege-dependent scripts) and static extraction of
used commands is undecidable (`eval`, computed command names). A partial or
advisory check is worse than none: it implies vouching the framework can't
back, and frequently-wrong warnings train users to ignore warnings. No
mainstream package manager verifies its manifests semantically either — a
wrong `pyproject.toml` just yields a broken package, and that's the accepted
norm.

**Consequences**:
- Good: no false confidence; no maintenance of a heuristic that's wrong on
  legitimate scripts; honest, simple contract.
- Bad: manifest errors are discovered by users at install/run time.

---

## D15. Inter-package dependencies are in v1

**Decision**: Packages can depend on other packages (semver ranges), from
v1. Resolution is server-side, returns the full transitive closure flat,
enforces single-version-per-closure, and cycles are rejected at publish.

**Reason**: Users will ask for composability almost immediately, and
retrofitting a resolver after the manifest and lockfile ship without one is
painful. Single-version-per-closure because side-by-side versions are
incompatible with a shared bin directory (D11); server-side because the
resolver should live in one place, not be reimplemented per client.

**Consequences**:
- Good: composable packages from day one; lockfile format is
  closure-complete from the start.
- Good: resolver iteration doesn't require client releases.
- Bad: this is the single largest v1 scope item — a real resolver, cycle
  detection, and range semantics — and moves the project well beyond "thin
  layer over Gitea".
- Bad: incompatible ranges across a closure become a user-facing error class.

---

## D16. Strict semver, enforced; npm-style yank; no hard delete

**Decision**: Versions must parse as semver or publish is rejected.
Removing a bad version means yanking: excluded from search and
`latest`/range resolution, still fetchable when pinned exactly. Published
versions are otherwise immutable.

**Reason**: Ranges, `update`, and the resolver (D15) only have well-defined
behaviour over semver; free-form versions break ordering (`1.10` vs `1.9`).
Hard delete breaks pinned consumers and contradicts content-addressed
immutability; npm's yank model preserves both safety properties.

**Consequences**:
- Good: deterministic ordering/ranges; broken versions removable from
  discovery without breaking anyone pinned.
- Bad: genuinely dangerous published content can't be fully removed through
  the normal mechanism (an out-of-band administrative path would be needed).

---

## D17. dnf-style install transaction flow

**Decision**: Install fully resolves first, then shows a transaction
summary — actions only (new installs, version changes, downgrades called
out; satisfied deps omitted), then shim conflicts presented distinctly and
naming each shim's current namespaced owner — then prompts. Interactive mode
is whole-transaction accept-or-abort; no per-item selection.

**Reason**: Multi-command packages widen the blast radius of D11's
last-wins rule; the user should see exactly what will change before it does.
Resolve-first avoids dnf's late-surfacing-conflict failure mode. Per-item
selection was rejected because it reintroduces partial-install ambiguity.

**Consequences**:
- Good: no silent clobbering; downgrades and overwrites are conspicuous.
- Good: install outcome is binary — fully applied or not at all.
- Bad: one more prompt in the happy path (addressed by D18 for automation).

---

## D18. `--force=no-conflicts` / `--force=all`; bare `-y` = no-conflicts

**Decision**: `-y`/`--yes` auto-accepts the transaction but aborts it
entirely (nothing installed, non-zero exit) on any shim conflict; it is
shorthand for `--force=no-conflicts`. `--force=all` accepts everything,
logging each overwritten shim. `--force` always takes an explicit value —
there is no bare `--force` flag. Chosen over apt-style all-or-nothing `-y`.

**Reason**: Shims are execution-shadowing PATH entries; silently replacing
one from a CI script is a real incident pattern (apt's `-y` has caused
exactly this). Splitting the flag keeps scripted installs safe by default
while keeping full automation available explicitly. Abort-rather-than-skip
on conflict keeps the D17 invariant that exit codes never mean "partially
installed". Bare `--force` (originally specified as a second no-conflicts
synonym) was dropped at implementation: an option that is both a flag and
takes a value only accepts the value in `=` form, so `--force all` would
silently parse `all` as a positional argument — an ambiguity worse than
requiring the value.

**Consequences**:
- Good: safe-by-default automation; conflicts fail loudly, not silently.
- Good: `--force=all` leaves an audit trail of what it clobbered.
- Bad: CI jobs hitting a legitimate conflict need a deliberate flag change
  to proceed — friction, but intended friction.

---

## D19. `scripticus new` scaffolding, `main.<ext>` convention, editable installs

**Decision**: `new <lang> <pkg>` scaffolds the D7 layout with a
language-appropriate entrypoint. Entrypoint rules: no `[commands]` →
`src/main.<ext>` (extension per language; not extensionless `main`) runs as
the package name; `[commands]` present → each entry maps command name →
script path and every entry gets a shim. Package names are kebab-case
(enforced at publish); script filenames follow their language's conventions.
An editable install mode points shims at the working directory.

**Reason**: `main.<ext>` decouples the entrypoint from the package name,
resolving the conflict between kebab-case package naming and
language-specific script conventions (e.g. PascalCase `.ps1`), while keeping
editor/language-server support that an extensionless file loses — Cargo's
`src/main.rs` is the precedent. Kebab-case-only at publish avoids PyPI's
retrofitted PEP 503 normalisation. Editable installs cover the
"debug the installed experience" loop without publish cycles.

**Consequences**:
- Good: naming rules are consistent and enforceable; no
  dash/underscore/case collision class.
- Good: dev loop stays "cd and run", with the installed experience one flag
  away.
- Bad: `main.<ext>` is a convention users must learn (mitigated: `new`
  generates it).

---

## D20. Local file installs with provenance tracking

**Decision**: `install -f|--file <archive>` installs from a local archive.
Install state records provenance (remote vs local); `update` skips
local-provenance packages with a warning.

**Reason**: Pip precedent; needed for development and air-gapped cases.
Without provenance tracking, `update` would error against a registry that
was never the source.

**Consequences**:
- Good: dev and offline workflows supported; `update` behaves sensibly.
- Bad: locally-installed packages bypass server-side validation entirely.

---

## D21. Verbatim manifest stored alongside a queryable projection

**Decision**: The index stores each published manifest verbatim, plus
publish-time-extracted relational tables (dependencies, commands, platform
tags, tool deps) for querying. Extracted columns are a projection of the
manifest: never independently editable, always re-derivable from the blob.
The blob is authoritative; the columns exist to be searched.

**Reason**: Search/resolution need indexed columns; auditability and
forward compatibility (manifest fields the schema doesn't yet extract) need
the original document. Doing both with a fixed authority rule — the
crates.io pattern — prevents index-vs-package drift.

**Consequences**:
- Good: manifest-aware queries without losing the canonical record; schema
  can extract new fields later by re-projecting existing blobs.
- Bad: mild duplication, and projection code must be kept faithful.

---

## D22. Dependency graph as plain relational rows, resolved on demand

**Decision**: Dependencies are ordinary rows keyed by package_version;
resolution traverses them (recursive query or in-application) per request.
No materialised transitive closures, no graph database.

**Reason**: At internal scale (hundreds to low thousands of packages),
traversal is instantaneous; crates.io serves far larger catalogues from
relational rows plus an in-memory resolver. Precomputation is complexity
without a measured justification.

**Consequences**:
- Good: simplest possible resolver substrate; nothing to keep consistent.
- Bad: if scale ever grows orders of magnitude, resolution cost must be
  re-measured — recorded here so any later "optimisation" starts from a
  deliberate baseline rather than an assumption.

---

## D23. Yank is a whole-version flag in v1; SQLite first, Postgres path open

**Decision**: Yank is a boolean on package_version (all variant artifacts
yanked together); per-variant yank is deferred. The database is SQLite,
accessed via SQLAlchemy with no SQLite-specific behaviour relied upon, so
Postgres remains a configuration change.

**Reason**: Whole-version yank is simpler to reason about, and adding
per-variant later is a painless schema addition, whereas starting
per-variant and simplifying back is not. SQLite keeps the deployment to two
containers with no database service (serving the easy-standup goal), and
the workload — read-heavy, publishes rare — is comfortably within its
envelope; Gitea itself follows the same SQLite-default/Postgres-optional
pattern.

**Consequences**:
- Good: minimal ops footprint; migration path preserved by discipline
  rather than rework.
- Bad: a single broken variant can only be yanked by yanking the whole
  version until per-variant support is added.

---

## D24. Gitea is never mirrored for ACLs; no server-side install tracking

**Decision**: The index service owns manifest-derived data and publish
records only. The namespace table is a cache/FK anchor; every publish
re-checks namespace existence and publish rights against Gitea live.
Nothing ACL-shaped is stored locally. There is no install or download
tracking table.

**Reason**: Permissions stored in two places disagree; delegating live
keeps Gitea singularly authoritative (consistent with D2/D4). Nothing in v1
needs the server to know who installed what — install state is the client's
lockfile, and downloads bypass the index service (D9).

**Consequences**:
- Good: no permission-drift class; no privacy/retention questions about
  usage data that nothing consumes.
- Bad: publish latency includes a live Gitea round-trip (negligible at
  publish frequency); usage metrics, if ever wanted, require deliberate new
  design interacting with D9.

---

## D25. The package manifest file is named `meta.toml`, not `scripticus.toml`

**Decision**: The manifest at a package's root is `meta.toml`. This renames
the `scripticus.toml` filename originally chosen in D6.

**Reason**: A file named after the tool suggests *configuration of
Scripticus* — and Scripticus configuration genuinely exists elsewhere
(`~/.scripticus/config.toml`, org-distributed via D12). `meta.toml` cleanly
disambiguates the two kinds of file: the manifest is metadata *about the
package*, not settings *for the tool*. The name says what the file is rather
than who reads it.

**Consequences**:
- Good: no ambiguity between package metadata and Scripticus configuration;
  the distinction survives future config files without further renames.
- Bad: the generic name doesn't identify the consuming tool on sight —
  encountering `meta.toml` in a repo doesn't tell you Scripticus is involved
  (a branded name would), and other ecosystems could plausibly use the same
  filename for their own purposes.

---

## D26. `pack` emits one archive per format group of the declared targets

**Decision**: Packing a package produces one archive per archive-format
group its manifest targets: a `.tar.gz` covering the POSIX/macOS targets
(platform tag e.g. `linux.macos`), and a `.zip` covering Windows. A package
targeting both groups yields two archives with identical content; one
targeting a single group yields one.

**Reason**: D7 fixes the format per platform but is silent on
multi-platform packages. A single archive would force one group to consume
the other's format; one archive per OS would duplicate byte-identical
content for linux and macos for no benefit. Per-format-group is the minimal
set in which every target platform receives its native format.

**Consequences**:
- Good: consumers always get their platform's conventional format; no
  redundant identical archives within a format group.
- Good: the archive set is a pure function of the manifest's platform list.
- Bad: a cross-platform package publishes two artifacts whose content is
  identical, differing only in container — mildly redundant in storage, and
  the content hash (D3) covers the directory tree, so both containers carry
  the same content identity.

---

## D27. Concrete tree-hash algorithm: sha256 Merkle over names + contents, modes excluded

**Decision**: The D3 content hash is computed as: each file hashes to
sha256 of its raw bytes; each directory hashes to sha256 of its entry
listing — one record per child, `blob|tree <hex> <byte-length>:<name>\n`,
sorted by name, with the name's UTF-8 byte length prefixed — and the
package identity is the root directory's hash, rendered `sha256:<hex>`.
File modes (including the executable bit) are **not** part of the hash.
`pack` additionally rejects file names containing control characters.

**Reason**: Git-style Merkle hashing gives a stable identity for arbitrary
trees. The length prefix makes the listing encoding injective: without it,
a file name containing a newline could embed forged listing records,
letting two distinct trees hash identically — fatal for an identity that
signing and server-side verification will anchor to (D1). Git solves the
same problem with NUL-terminated binary records; a length prefix does the
job while keeping the listing printable. The control-character check is
belt-and-braces UX, not a hash-safety requirement: such names are never
intentional in a script package, so rejecting them at pack time gives
authors a clear early error. Modes are excluded, unlike git, because zip
extraction drops the executable bit: the same content must hash identically
whichever archive container it travelled in (a cross-platform package ships
as both `.tar.gz` and `.zip` per D26), and shims invoke the interpreter
explicitly (D11) so nothing functional depends on the bit.

**Consequences**:
- Good: container-independent and platform-independent identity; the
  algorithm is small enough to reimplement server-side identically (it must
  be — the index will verify it at publish).
- Good: distinct trees provably hash differently; no filename can forge
  listing structure.
- Bad: a change that only flips a file's executable bit does not change the
  package's identity.

---

## D28. Uninstall offers orphaned-shim replacements; never re-points silently

**Decision**: When uninstalling a package that owns command shims, the
client searches the other installed packages' manifests for ones that also
provide those commands. Interactively, each orphaned command gets a numbered
picker — option 0, the default, is "No replacement" — and a selection
re-points the shim and records the new ownership in the lockfile. Under
`-y`/`--yes` nothing is ever re-pointed: a single alternative provider is
hinted with the exact `scripticus use` invocation to restore the command; with
several providers they are listed, followed by a note that no replacement is
selected by default. Providers are discovered by re-reading the installed
manifests under `pkgs/`, not from lockfile state.

**Reason**: Last-install-wins (D11) means a lock entry records only the
commands a package *currently owns*, so uninstalling a shim owner would
silently drop a command that another installed package still provides — the
worst outcome is the user discovering a missing command later with no signal
that a one-line fix existed. The picker surfaces the choice at the moment it
arises; it is the interactive front-end to the same re-point primitive `use`
(D11) exposes explicitly. Re-deriving providers from manifests keeps the
lockfile from carrying a second copy of manifest-derived data that could
drift (the D21 instinct applied client-side) and works for packages
installed before this behaviour existed. Non-interactive runs never
re-point because choosing a replacement on the user's behalf would make
`-y` mean "yes, and also decisions I never saw". The install transaction's
"no per-item selection" rule (D17) is not contradicted: that rule prevents
partial-install ambiguity, whereas replacement selection happens after a
completed uninstall and cannot create partial state.

**Consequences**:
- Good: uninstalling a shim owner can no longer silently orphan a command
  another package provides; the user always gets either a choice or a hint.
- Good: the re-point primitive is shared with the future `use` command.
- Bad: uninstall now reads every other installed package's manifest — fine
  at CLI scale, and a damaged tree is skipped rather than blocking removal.

---

## D29. Cross-cutting code ships as `scripticus-schema`, scoped to the contract

**Decision**: Code needed on both sides lives in a third workspace member,
`schema/`, published to PyPI as `scripticus-schema`. Its contents are the
contract and nothing else: the Pydantic manifest schema and validation
(D13), the package/namespace naming rules and language table, strict-semver
parsing and ordering (D16), the D3/D27 tree-hash implementation, and — once
the API is designed — the wire-format models. Admission rule: **code goes
in `schema/` only if it defines what a package is or how client and server
communicate.** Anything convenience-shaped ("both sides could use it")
stays out. Client and server declare it as a normal dependency with tight
same-minor version bounds, resolved from the workspace during development
(`[tool.uv.sources] … workspace = true`).

**Reason**: D13's deciding argument for one language was one shared
manifest schema; D27 requires the client and server tree-hash
implementations to be bit-identical — sharing the module makes that true by
construction instead of by discipline. The name is deliberately narrow:
built wheels do not vendor workspace members, so every module in this
package ships as a third published PyPI artifact whose changes force
coordinated three-package releases. A grab-bag name (`shared/`, `common/`)
advertises for exactly the convenience code that would inflate that
coupling; `schema` states the admission rule in the directory listing.

**Consequences**:
- Good: manifest, identity, and version-ordering rules cannot drift between
  client and server.
- Good: the admission rule keeps the package — and the release coupling —
  small.
- Bad: a third PyPI package to publish, and ordering matters: a client or
  server release that bumps the schema dependency requires the schema
  release to be on the index first.
- Bad: a schema change fans out to up to three coordinated version bumps.

---

## D30. The read API's wire models are the first pinned-down API schemas

**Decision**: The read endpoints — `GET /packages/{namespace}/{name}`
(version listing) and `GET /search` (name substring plus optional
platform/language filters) — get the first designed API schemas, as
response models in `scripticus_schema.index_api` (per D29). The models
encode the contract itself: version listings are newest-first by semver
precedence (D16) with yanked versions included and marked; search
excludes yanked versions entirely (npm-style — a fully-yanked package
does not appear at all).

**Reason**: The read path has the simplest contract and no write-side
entanglements (auth, atomicity, Gitea), so designing it first forces the
index data model into existence without blocking on publish design.
Encoding the ordering and yank semantics in the schema package makes
them contract, not server implementation detail.

**Consequences**:
- Good: publish and resolve build against an existing data model and an
  established home for API shapes.
- Good: yank visibility rules are written down once, in the contract.
- Bad: the server now depends on `scripticus-schema`, so D29's
  schema-releases-first ordering applies to server releases too.
- Bad: publish and resolution shapes remain undesigned; this decision
  deliberately does not constrain them.

---

## D31. Index tables via `create_all` until the schema has released consumers

**Decision**: Tables are created with SQLAlchemy's `create_all`
(idempotent, on first database use); no migration tool yet. Alembic (or
equivalent) is adopted when a schema change would strand data someone
cares about.

**Reason**: A migration history for tables nobody has populated is
ceremony without users, during the data model's most change-heavy
period. The index is re-derivable from stored manifests anyway (D21),
so early drop-and-recreate is cheap.

**Consequences**:
- Good: the data model can evolve freely while it is young.
- Bad: a deployment that outlives a schema change must recreate its
  database until migrations arrive — this decision has an expiry date.

**Revisited when publish landed (D32)**: `create_all` stays — populated
indices are now possible but not yet precious. Alembic arrives with the
first persistent deployment, and in any case before v1.0.0.

---

## D32. Publish: pass-through Gitea auth, derived-from-archive validation, format-variant rule

> **Superseded in part by [D37](#d37-batch-publish-one-multipart-request-for-a-versions-whole-archive-set-atomic-across-the-batch).**
> `POST /packages` now accepts a batch of one or more archives per
> request, validated and committed atomically as a set. Everything else
> below stands unchanged.

**Decision**: Publish is `POST /packages`: a multipart archive upload
with the caller's own Gitea token in the `Authorization` header. The
server derives everything — identity, platforms, language, dependencies,
commands, content hash — from the archive; no client claim is trusted
(D8). The token is pass-through: publish permission is checked live
against Gitea (D24) and the blob is stored as the caller; the service
holds no credentials of its own. Ordering gives atomicity: validate,
upload the blob, commit the index record only after Gitea confirms; a
commit failure triggers a best-effort blob delete. Versions are
immutable with one carve-out, the format-variant rule: an existing
version accepts an additional artifact only when the tree hash matches
the recorded one and the format is not yet present (exactly what D26's
per-format packing produces). An archive's format must match the
declared platforms, the `library` namespace is rejected (D5), and only
the response model (`publish_api.PublishResult`) is contract.

**Reason**: Pass-through auth is the smallest design satisfying D24 —
permission truth stays in Gitea, and the service cannot leak credentials
it never holds. Deriving everything server-side kills
index-says-X-package-says-Y inconsistency at the door (D21 applied to
ingest). Blob-then-record ordering makes the worst crash an orphaned
blob (invisible to resolution), never an index record pointing at
nothing (broken installs).

**Consequences**:
- Good: no server-held credentials, cached ACLs, or trusted client
  claims; failures degrade toward "publish rejected", never "index
  corrupted".
- Bad: a CI publisher needs a personal Gitea token; scoped publish
  tokens remain deliberately undesigned.
- Bad: an orphaned blob is possible if the best-effort delete also
  fails; harmless, but manual cleanup in Gitea's UI.

---

## D33. Publish-time dependency rules: targets must exist; cycles rejected

**Decision**: A declared dependency must be a fully namespaced
`namespace/name` already present in the index with at least one version
(the crates.io rule). Publish also rejects a version that would make
the publishing package reachable from itself in the package-level
dependency graph (edges: the union of every version's declared
dependencies).

**Reason**: The existence check catches typos while the author can fix
them and makes forward references — the raw material of cycles —
impossible to mint. Package-level granularity matches how resolution
installs (single-version-per-closure); union-of-versions edges
deliberately over-approximate, because a false rejection is a clear
publish-time error while an admitted cycle is a confusing resolution
failure for someone else later.

**Consequences**:
- Good: the resolver can assume an acyclic package graph.
- Good: dangling dependency references cannot enter the index.
- Bad: mutually-referencing packages must be published in dependency
  order — same as crates.io, inherent to the rule.

---

## D34. `login` stores a Gitea token per remote, cargo-style; verification at login deferred

**Decision**: `scripticus login` prompts for a Gitea personal access
token (package-write scope, minted in Gitea) and stores it verbatim in
`~/.scripticus/credentials.toml`, keyed by the remote's URL, permissions
0600 (grammar and remote registration: D35). No username is stored —
publish replays the bare token in the `Authorization` header (D32).
`SCRIPTICUS_TOKEN` overrides the stored token (the CI path). Credentials
never live in `config.toml`, which is org-distributable (D12). The token
is stored unverified; a whoami-style verification endpoint (D24's
live-check pattern) is planned follow-up — recorded here so the gap
reads as sequencing, not oversight.

**Reason**: The model every comparable tool converged on (`cargo login`,
`npm login`, `docker login`): plaintext with tight permissions is the
accepted precedent, honest about the threat model — whoever can read the
file can read SSH keys too. Deferring verification is safe because
publish must handle stale tokens with an actionable "re-run login" error
regardless.

**Consequences**:
- Good: D32 unchanged — the index service still holds no credentials.
- Good: CI publishing works today via `SCRIPTICUS_TOKEN`, without
  designing scoped tokens.
- Good: the file split makes leaking a token through `config install`
  structurally impossible.
- Bad: a plaintext token on disk; OS-keyring integration is possible
  later but not designed.
- Bad: until verification lands, a mistyped token surfaces at first
  publish rather than at login.

---

## D35. Named ordered remotes (`[[remotes]]`); publish defaults to the first; `login` doubles as first-time remote registration

**Decision**: `config.toml` holds remotes as a TOML array of tables,
`[[remotes]]`, each `{ name, url }`; array order remains D5's
search-path priority. `publish` targets the first remote unless
`--remote <name>` names another — no separate `default_remote` setting.
`login <name>` looks the name up and prompts for a token (stored per
D34, keyed by URL); an unknown name fails, naming the two-argument
form. `login <name> <url>` also registers the remote (appended, lowest
priority) when absent; an existing name with a *different* URL is
refused outright — login never re-points a remote — while the same URL
is accepted as redundant confirmation.

**Reason**: An ordered array-of-tables is the only TOML shape that
expresses priority without a second ordering field, and defaulting
publish to the first entry reuses that order instead of adding a knob.
Login doubling as registration avoids a separate `remote add` command
for what is normally a one-shot event; the URL-conflict refusal stops an
authentication command from silently moving a remote.

**Consequences**:
- Good: one mechanism (list order) does both jobs; nothing to keep in
  sync.
- Good: first-remote onboarding is one command, and URL-keyed
  credentials (D34) survive remote renames.
- Bad: the two-argument form does two jobs — a mild surprise, mitigated
  by documenting it plainly.
- Bad: `login` appending to an org-distributed `config.toml` (D12) lets
  local files drift from the baseline; `config install` remains the
  reset.

---

## D36. `publish <path-prefix>` targets pre-built archives by name-version; stop-on-first-failure by default

> **Superseded in part by [D37](#d37-batch-publish-one-multipart-request-for-a-versions-whole-archive-set-atomic-across-the-batch).**
> With batch-atomic publish there is no partial success left, so the
> stop-on-first-failure default and `--continue-on-error` below no
> longer apply. The `<path-prefix>` argument, structured filename
> matching, dash/underscore normalisation, and `--remote` stand
> unchanged.

**Decision**: `publish` never invokes `pack`. It takes one positional
argument — a path whose final component is a `<name>-<version>` prefix
(e.g. `some/dir/my-cool-script-0.1.2`) — and operates on every archive
in that directory whose D26 filename carries exactly those name and
version fields. Matching is structural, not `startswith()` (which would
match `0.1.20` against `0.1.2`), with dash and underscore equivalent so
the canonical dashed name matches the filename's mangled form. Archives
publish in sorted order; the first failure aborts the rest and the
command exits non-zero; `--continue-on-error` attempts every archive
and reports each outcome.

**Reason**: Separate pack and publish steps keep each command doing one
job, and the path argument reuses the identifier `pack` already prints.
Stop-on-first-failure was the closest a per-archive loop could get to
atomicity — D16's immutability means an already-published archive
cannot be undone — with D32's format-variant rule making a plain re-run
the idempotent recovery.

**Consequences**:
- Good: publish's only input concern is "which archives", decoupled from
  how they were produced.
- Good: recovery from partial failure is a plain re-run.
- Bad: stop-on-first-failure is not atomicity — an already-succeeded
  archive stays published (the gap D37 later closed).
- Bad: filename matching depends on D26's naming scheme staying stable.

---

## D37. Batch publish: one multipart request for a version's whole archive set, atomic across the batch

**Decision**: `POST /packages` accepts one *or more* archives per
request, revising D32's exactly-one. The server stages and validates
every archive — batch cohesion (identical content hash, no duplicate
format) plus D33's checks, once per batch — before any Gitea write;
blobs upload only after everything validates; the index record commits
only after every upload confirms; failure anywhere rejects the whole
batch, best-effort deleting any blob already uploaded.
`PublishResult.artifact` becomes `artifacts`, a list. Client-side,
`publish <path-prefix>` (D36) sends every matched archive in this one
request, dropping stop-on-first-failure and `--continue-on-error`. A
later publish adding a format to an existing version remains a batch of
one, governed by D32's format-variant rule unchanged.

**Reason**: The server already staged one archive in a temp directory
before touching Gitea; N archives inside one request is a small
generalisation, not new architecture. It closes a gap D36 could only
manage: a client-side loop can never undo an archive that already
landed (D16 — no hard delete), so moving the atomicity boundary to
"everything the client meant to publish together" removes the partial
state at its source.

**Consequences**:
- Good: a multi-format publish is fully live or not live at all.
- Good: D33's checks become deliberately once-per-batch rather than an
  accident of request ordering.
- Good: D36 sheds a flag and a failure-reporting design; the client's
  job shrinks to "match, send, report one outcome".
- Bad: revises a shipped endpoint, response schema, and test suite —
  not greenfield.
- Bad: a mid-upload network failure costs the whole batch; accepted for
  small script archives.

---

## D38. Three-tier command shims: a guaranteed-unique tier, convenience tiers, and no `run` command

**Decision**: Each command installs three shims into D11's bin
directory: `<namespace>.<package>.<command>` (structurally unique —
(namespace, package) is unique registry-wide, command names unique
within a package), `<namespace>.<command>`, and bare `<command>`. The
two convenience tiers may collide; collisions follow D11's
last-install-wins rule with the existing D17/D18/D28 surfacing, and
`use` re-points a convenience shim by its literal name (dot count
selects the tier; a namespaced shim only re-points within its
namespace). The fully-qualified tier is never re-pointed; convenience
shims point directly at it. Dot count identifies a shim's tier because
`.` is excluded from every identifier character set. The planned `run`
command is dropped, and the post-v1 dot-qualified-invocation roadmap
item is promoted into v1 in this stronger form (its two-segment sketch
left same-namespace collisions open).

**Reason**: `run` would be a subprocess wrapper, re-implementing what
PATH execution gives a shim for free (arguments, exit codes, TTY,
signals — on both POSIX and Windows); with the fully-qualified tier,
"namespaced invocation always available" (D11) is structural instead.
Three tiers rather than two because command names are unique only
within a package, so `<namespace>.<command>` alone can still be
contested; with a unique tier underneath, one collision rule covers
every convenience shim. Precedent: `python3.11` alongside `python3`.

**Consequences**:
- Good: every installed command is always invocable, with no invocation
  code path to write or test.
- Good: one collision rule at every tier; D17/D18/D28 generalise rather
  than growing a parallel mechanism.
- Good: one command fewer — `run`'s unsettled grammar never needs
  settling.
- Bad: three shims per command make `ls ~/.scripticus/bin` noisier.
- Bad: lockfile ownership changes shape (per-tier shims, not a flat
  command list) and the shipped install/uninstall/use code must be
  revised.
- Bad: a default-entrypoint package `foo/a` yields the ugly `foo.a.a` —
  accepted; it is typed only when both convenience tiers are contested.

---

## D39. `init` edits one `$SHELL`-chosen profile file; Windows user PATH via registry

**Decision**: `scripticus init` puts the bin directory on the persistent
PATH by appending one marked `export PATH=...` line to a single profile
file — `~/.zshrc` for zsh, `~/.bashrc` for bash, `~/.profile` otherwise —
or, on Windows, by appending to the per-user `Path` value in
`HKCU\Environment` (not `setx`, which truncates at 1024 characters).
Idempotent by inspection: nothing is written when the bin directory
already appears on the live `PATH` or in the target file/value. It also
pre-creates the state skeleton (`~/.scripticus/bin/`) and tells the user
to restart their shell.

**Reason**: pip cannot edit a shell profile, so the "added to PATH once
at install time" step D11 assumes needs a command (the `pipx ensurepath`
pattern). One predictable file per shell beats userpath-style multi-file
writes: easy to inspect, one line to remove to undo, and `$SHELL` is the
best available signal for which file is actually read.

**Consequences**:
- Good: idempotent and inspectable; re-running is always safe.
- Good: manual setups are respected — an existing PATH entry, however
  written, suppresses the edit.
- Bad: single-file simplicity mishandles edge cases — macOS bash login
  shells read `.bash_profile`, and fish doesn't read `.profile` — those
  users add the printed line themselves.

---

## D40. `GET /whoami`: pass-through token verification for `login`

**Decision**: The index service exposes `GET /whoami`, taking the
caller's Gitea token in the `Authorization` header, passing it straight
through to Gitea's own `/user` (the same live check publish already runs,
D24/D32), and returning the token owner's login as
`scripticus_schema.whoami_api.WhoAmI` (D29 — only the response shape is
contract). A missing or rejected token is 401 with Gitea's verdict
passed through; Gitea unreachable is 502. It reuses `gitea.py`'s
`get_gitea_client` dependency and `authenticated_user()`, so nothing new
speaks HTTP to Gitea and unit tests fake it exactly as publish does. This
is the server half of D34's deferred verification; the client's use of it
at `login` time is separate follow-up.

**Reason**: D34 stores a token unverified, so a mistyped token surfaces
only at first publish; the smallest fix is a read-only echo of the check
publish already makes. Pass-through keeps D24 intact — the service holds
no credentials, caches nothing ACL-shaped, and stores nothing from the
response.

**Consequences**:
- Good: the client can verify a token the moment it is entered, closing
  D34's noted gap without new auth machinery.
- Good: reuses the existing Gitea boundary and its fake — one auth code
  path, tested unit-side and behind the `e2e` marker like publish.
- Bad: another live Gitea round-trip per login; negligible, and the
  point of the endpoint.

---

## D41. `login` verifies the token via `/whoami` before storing it; unreachable refuses

**Decision**: `scripticus login` calls the target remote's `GET /whoami`
(D40) with the freshly entered token before writing `credentials.toml`,
and stores the token only if it authenticates. Success confirms with the
authenticated Gitea login (`Logged in to <remote> (<url>) as <user>`). A
rejected token (401) and an unreachable remote are reported as distinct
errors — the latter says the token may be fine and the remote merely down
— but both refuse the login and write no credential. The stored-file
format is unchanged (D34): this alters only what `login` does before
writing. Because config is written before the token prompt (to fail early
on an unwritable config), a first-time `login <name> <url>` whose token is
then rejected leaves the remote registered but stores no token; a plain
re-login recovers. This is the client half of D34's deferred verification,
completing it against D40.

**Reason**: Verifying at entry turns a mistyped token from a confusing
first-publish failure into an immediate, clear login error — the
docker/npm-login UX D34 anticipated. Refusing on an unreachable remote is
the simpler contract than storing-with-a-warning: an unverified token on
disk is exactly the state verification exists to prevent, and the retry
cost is one command.

**Consequences**:
- Good: bad credentials are caught at the moment and place they are
  entered, with the authenticated identity shown back as confirmation.
- Good: no new wire surface or storage change — reuses D40 and the
  existing credential store.
- Bad: `login` now requires the remote to be reachable, so it can no
  longer be completed fully offline (previously it never contacted the
  remote at all).
- Bad: a first-time registration whose token is rejected leaves the
  remote in `config.toml` without a credential — recoverable by
  re-login, but a mild surprise.

---

## D42. Resolution: server-side solver over the client's installed state; resolve, then fetch direct from Gitea

**Decision**: Installing from a remote is two phases. First, `POST
/resolve` takes the root package (name plus optional version spec), the
client's platform, and the client's installed closure as **identities
only** — each installed package as `namespace/name@version`, no
constraints; the server re-derives each installed version's constraints
from its own index (D21, and D33 keeps every dependency edge within one
index, so the remote being resolved against already holds every
constraint that could matter). This keeps the request a function of the
installed count alone (~tens of bytes each), and the response bounded by
the root's closure rather than the installed set. A locally-installed
package (`install -f`) is in no index; today it cannot declare package
dependencies, so it does not participate — if that is ever relaxed, its
constraints would have to be sent explicitly. `/resolve` returns the
fully resolved set: one version per package
(single-version-per-closure), each entry carrying its content hash, Gitea
download pointer, and a direct/transitive marker, plus the aggregated
tool requirements (D43). The server runs the only solver: it walks the
dependency graph (acyclic by D33), consolidates each package to one node,
and picks the highest version satisfying the intersection of every
constraint reaching it, treating the installed packages as hard
constraints — so a resolve never bumps a package in a way that breaks
something already installed, and prefers an installed version that still
satisfies. An empty intersection is a hard error naming the package and
the conflicting constraints; versions never coexist side by side. Second,
the client fetches each resolved blob straight from Gitea (D9) with its
stored token, verifies the tree hash (D3), and installs through the
existing transaction flow (D17) — the plan/confirm/prompt boundary sits
between resolve and fetch, so every feasibility check runs before any
mutation. Downloads stage-then-commit (all blobs fetched and verified
before any unpack/shim) to preserve "never partially installed" (D17).
The intersection-and-pick-highest step is a reusable version-window
primitive, applied here to packages against the index and by D43 to
tools. The resolve response is contract (a new `scripticus_schema`
model); the solver's internals are not.

**Reason**: The index already holds the whole graph, so the solver
belongs where the data and the publish-validation code are — one tested
implementation, not one shipped in every CLI where version skew could
diverge. Passing the installed closure up delivers installed-aware
resolution without a client-side solver. Reads bypassing the index
service (D9) mean resolve needs no companion download endpoint — its
pointers are Gitea URLs. Single-version-per-closure keeps resolution a
forcing function that surfaces incompatibilities as upgrade/publish-order
errors rather than silent sprawl, and matches the shared bin dir (D11).

**Consequences**:
- Good: one solver, server-side, unit-tested beside publish validation;
  the client stays a planner/installer.
- Good: installed state is honoured (no needless churn, no cross-install
  breakage) without shipping resolution logic client-side.
- Good: no second endpoint for downloads; resolve pointers are paths the
  client fetches from the same front URL, routed to Gitea (D45).
- Bad: the client uploads its installed inventory on every resolve — fine
  within an org, more coupling than a pure GET.
- Bad: an unsatisfiable window is a hard failure with no side-by-side
  escape hatch; version-qualified shims (`ns.pkg.X.Y.Z.cmd`, with D38's
  fully-qualified tier as the hook) stay a post-v1 option.
- Bad: revises the roadmap's "server returns the closure from the root"
  into "resolves given the client's installed state."
- Note: a directly-installed, user-pinned package may be version-changed
  when a new install's transitive need requires it (the installed version
  is a preference, not a pin the solver may not cross); this surfaces as a
  version-change in the transaction plan, never a silent bump.

---

## D43. Tool-dependency resolution splits across the boundary; version windows are a fast-follow

**Decision**: System tool requirements resolve in two halves, because the
server cannot see a client's package manager. The server computes, via
the same version-window primitive it uses for packages (D42), the
required window per tool across the resolved closure (required vs optional
preserved) and returns them in the resolve response. The client checks
those windows against its local package manager — satisfiability (can the
PM provide an in-window version) and conflict (against already-installed
tools) — before anything is installed, then installs the accumulated tool
set in one pass during apply. v1 ships this name-only: the manifest still
models tools as bare `requires`/`optional` name lists, so the "window"
degenerates to presence/installability with conflict on the name. Versioned tool constraints — a manifest and `scripticus_schema`
extension allowing e.g. `git = ">=2.30"`, run through the shared
windowing — are a fast-follow, deliberately not on the resolver's v1
critical path.

**Reason**: Only the client knows what its PM offers or has installed, so
satisfiability and conflict detection are inherently client-side; only
the server sees the whole closure, so window aggregation belongs there —
the split follows the information, not convenience. Deferring tool
versions lands the resolver without dragging a manifest/schema change
onto its critical path, and because the window primitive is shared,
adding them later is data-plumbing, not new algorithm.

**Consequences**:
- Good: the version-window algorithm is written once and reused for
  packages and tools.
- Good: tool feasibility is proven before any package or tool is
  installed, so D17's "never partially installed" holds through the tool
  path too.
- Bad: system-PM installs are not cleanly rolled back, so a later apply
  failure can leave tools behind — benign additions, unlike package
  files/shims, which stage-then-commit.
- Bad: name-only tools in v1 cannot express "needs git ≥ 2.30"; the
  schema extension is deferred, not free.

---

## D44. Tool installation shells out to an operator-configured command; no package-manager enum

**Decision**: System-tool installation runs a command the operator sets
in `config.toml` (`[tools] install`), not package-manager logic Scripticus
encodes. On apply, the required tools not already on `PATH` are
substituted into that command — a `{packages}` placeholder (shell-quoted,
space-joined; appended if absent) — and it runs once through the platform
shell (`bash -lc` / `cmd /c`), inheriting the process environment.
Scripticus itself never requires elevated privilege: its own state is
user-space (`~/.scripticus`, D11/D39), so `install`/`uninstall` of a
package and its shims run as the invoking user, and `uninstall` never
touches system tools. The *only* action that may need root is the tool
command, and it is elevated in isolation by an optional machine-configured
prefix, `[tools] escalate` (e.g. `"sudo"`, `"doas"`, or empty when already
root / on Windows-as-admin), prepended to the tool command alone. With no
`[tools] install` configured, Scripticus never invokes a package manager —
missing *required* tools abort the install listing them (with a
`--skip-tools` escape), missing *optional* tools are only reported. The
tool command runs **before any package file or shim is written**, so a
tool failure aborts the install before package mutation begins — the v1
guarantee behind "check tools before installing packages" (a non-mutating
dry-run cannot be synthesised from an opaque command, which may be
non-interactive; a configured `[tools] check` dry-run is the post-v1 way
to get one). v1 "satisfiability" is PATH presence only; an install-only
command cannot be queried for versions, so versioned tool windows and an
optional query/check command are post-v1 (D43). Tool names come from third-party
manifests, so they are validated to `[A-Za-z0-9][A-Za-z0-9._+-]*` at
manifest parse and shell-quoted at invocation — a manifest cannot inject
shell.

**Reason**: Encoding dnf/apt/apk/snap/choco/winget and their flags, pin
syntax, and confinement rules is a matrix we would own forever, and in an
enterprise it is useless without an override for proxies, mirrors, and
credentials — so the override is the whole feature (D2/D14: offload to the
substrate, do not reimplement it). The shell expands env vars for free, so
proxies and credentials stay in the machine environment while the command
stays in the org-distributable config (D12), secret-free. Scoping
elevation to a machine-set prefix on the tool command — rather than
running all of `scripticus install` as root — keeps the tool a
user-space program (no sudo for the common no-tools install, user state
never lands under root's home), keeps the base command portable across
sudo/doas/root/Windows, and keeps privilege out of package-authored data.

**Consequences**:
- Good: zero package-manager code; any manager, including unknown ones,
  works if the operator can write its command line.
- Good: reads and package install/uninstall never need privilege; only the
  tool command elevates, and only when a required tool is missing.
- Good: proxies/mirrors/credentials come from the environment; the
  configured command carries no secrets and distributes cleanly (D12).
- Good: no package manager runs unless the operator opted in; third-party
  manifests cannot inject shell.
- Bad: no zero-config convenience — a machine with no `[tools] install`
  cannot auto-install tools (by design; examples ship in docs, not code).
- Bad: an escalate prefix that scrubs the environment (`sudo` does by
  default) can drop the proxy/credential env vars the command relies on —
  the operator preserves them via the prefix itself (`sudo -E`) or their
  sudoers config; Scripticus does not paper over it.
- Bad: PATH presence is coarse — a tool present but too old is not caught
  until versioned windows land (post-v1).

---

## D45. One front URL: a reverse proxy in the compose bundle routes to index + Gitea

**Decision**: The `docker-compose.yml` registry bundle gains a reverse
proxy as the single user-facing endpoint; the index service and Gitea sit
behind it on the internal network. A client points at one URL (the
remote's `url`) for everything: index calls (`/resolve`, `/search`,
`/packages/...`, `/publish`, `/whoami`) route to the index service, and
blob paths (`/api/packages/.../generic/...`) route to Gitea. Resolve
therefore returns download pointers as **paths relative to that same front
URL** — the client fetches `{remote.url}{pointer}` with its stored Gitea
token (the proxy forwards the `Authorization` header) and never learns
Gitea's internal address. Reads still bypass the index *application* (D9):
the proxy is dumb pass-through infrastructure, not the index app touching
blob bytes. The internal index→Gitea calls (auth, publish uploads) use the
internal address, not the proxy.

**Reason**: Exposing a second URL/port for direct Gitea downloads is
low-impact on paper but expensive in enterprise IT — new firewall holes,
per-endpoint authorisations, TLS certs. One front URL is one thing to get
approved. Keeping download pointers relative to it means the client needs
no Gitea address or second credential realm, and D9's "index off the data
path" survives because the proxy, not the index service, carries the
bytes.

**Consequences**:
- Good: a single URL/port to authorise, firewall, and put a cert on.
- Good: the client's download addressing is trivial — same origin as the
  index, same token; no Gitea URL to discover or configure.
- Good: D9 intact — the index application never proxies blob bytes.
- Bad: another moving part in the bundle, and a routing rule set that must
  keep index paths and Gitea paths from colliding (the index API can be
  mounted under a reserved prefix if a Gitea route ever clashes).
- Bad: split deployments that forgo the bundled proxy must reproduce the
  single-front routing themselves for relative pointers to resolve.

**Note (proxy choice)**: the bundle uses Caddy. The routing is a
two-way path split, which a Caddyfile expresses in ~10 declarative lines
(a named matcher plus two `handle` blocks, path preserved by default) from
a single static-binary image — the least config for the job, matching the
"trivial to stand up" ethos (D2/D23). nginx and HAProxy were the
alternatives: nginx needs more `http`/`server` scaffolding for the same two
routes, and HAProxy's ACL/backend model is aimed at multi-backend load
balancing this doesn't need. No requirement (many backends, advanced LB,
L4) favours the heavier options; Caddy's automatic-HTTPS is an unused-here
bonus if a public deployment later terminates TLS at the front. The proxy
is swappable behind the "one front URL" contract, so this binds nothing.

## D46. Client remote install: one remote per closure, fully-namespaced only in v1

**Decision**: `scripticus install <namespace/name>[@spec]` resolves
against the configured remotes in priority order, stopping at the first
whose index has the root package; the whole closure resolves there. This
is complete because D33 guarantees every publishable closure is
single-remote — a dependency target must already exist in the *same* index
at publish, so a closure can never span remotes. Cross-remote
dependencies are therefore unsupported in v1: a package needed from
another remote must be mirrored into the resolving remote (the
Artifactory/Nexus pattern); true federation across remotes is post-v1 and
is not precluded by D42's server-side resolver (it layers on as
client-side orchestration or server-to-server resolution later). v1
requires the fully-namespaced form; bare-name resolution via the D5
namespace search path is deferred (its config shape — how a namespace maps
to a remote — is not yet pinned). `--remote <name>` forces a specific
remote.

**Reason**: Closed-world-per-remote is what D33 already enforces, so
single-remote resolution is correct rather than a limitation invented
here, and it keeps the server-side resolver (D42) valid — no server needs
packages it does not host. Requiring fully-namespaced names for v1 sidesteps
the still-undesigned search-path config without blocking the read path,
since bare names are only a convenience over identities that are always
fully namespaced anyway (D4/D5).

**Consequences**:
- Good: resolution stays a single `/resolve` call to one remote; the
  server-side model holds unchanged.
- Good: v1 ships without settling the namespace-search-path config.
- Bad: no cross-remote dependency closures — shared/public deps must be
  mirrored until federation lands (post-v1).
- Bad: bare-name `install foo` does not work yet; users type
  `ns/foo` until the search path is designed.

---

## D47. `/resolve` returns each package's command map, so shim conflicts show before the prompt

**Decision**: The resolve response (`ResolvedPackage`) carries the
package's effective command→script-path map — the index's publish-time
projection of the manifest, default-entrypoint rule already applied. The
client uses it to compute the D17 transaction summary's shim conflicts and
to write shims, so nothing about a package's commands requires reading its
archive first.

**Reason**: D17 requires shim conflicts — each contested convenience
shim's current owner — in the summary *before* the confirm prompt, but a
new package's command names live in its manifest, inside the archive, and
D42 puts the blob fetch *after* the prompt. Without commands in the
response the client could not honour both. The index already stores the
map (the `command` table, D21), so returning it is a projection already
paid for, not new server work, and it keeps D42's fetch-after-prompt
boundary intact rather than pulling downloads before the prompt.

**Consequences**:
- Good: the pre-prompt summary is complete — version changes, tools, *and*
  shim conflicts — with no archive fetched (D42's boundary unmoved).
- Good: the client writes shims from the resolved map; the fetched
  manifest is only re-validated, not the source of the command list.
- Good (forward-looking): commands as first-class index-returned data is
  the raw material for a future `dnf provides`-style reverse lookup —
  "which package provides command X" — served straight from the index
  without opening any archive.
- Bad: widens the resolve wire contract (a D29 schema change, three-package
  coordination), and the map duplicates what the fetched manifest also
  carries — the resolved map is authoritative for the plan, the archive's
  own manifest for on-disk validation.

---

## D48. `search` queries every remote and merges, best-effort

**Decision**: The client's `search <query>` calls each configured remote's
`GET /search` (D30) in priority order and merges the hits, each tagged with
the remote it came from; `--remote` restricts to one. Unlike `install`, which
stops at the first remote hosting the root (D46), search fans out — the point
is to see what exists, not to pick one. Discovery is best-effort: a remote
that is unreachable or errors becomes a warning and the others' results still
show; only an all-remotes failure (or no remotes) is a hard error. The call
sends no token — `/search` is an anonymous read.

**Reason**: Search and install answer different questions. Install wants *the*
package to fetch, so first-match-wins is right; search wants the landscape, so
stopping early would hide mirrors and alternatives. Being resilient to one
down remote keeps discovery useful when part of a federation is offline —
whereas install must fail closed. The server already excludes yanked versions
and applies the platform/language filters (D30), so the client just renders.

**Consequences**:
- Good: completes the read path — the last client gap (search) is closed.
- Good: results are honest about provenance (remote-tagged), so mirrored or
  differing versions across remotes are visible rather than silently merged.
- Bad: search and install treat a down remote differently (warn vs. fail),
  a deliberate asymmetry to keep straight.
- Bad: no cross-remote dedup in v1 — the same package on two mirrors shows
  twice; a package appearing once per remote is the honest v1 behaviour.
