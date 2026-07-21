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

**Decision**: The index service's read endpoints —
`GET /packages/{namespace}/{name}` (version listing) and `GET /search`
(name substring plus optional platform/language filters) — are the first
part of the client/server API to have designed schemas. Their response
models (`VersionSummary`, `PackageVersions`, `PackageSummary`,
`SearchResults`) live in `scripticus_schema.index_api`, per D29's
admission rule. The models encode the read-path contract itself: version
listings are ordered newest-first by semver precedence (D16) and include
yanked versions marked as such; search results exclude yanked versions
entirely — a package's `latest_version` is its latest non-yanked version,
and a package with every version yanked does not appear (npm-style yank).

**Reason**: D29 anticipated wire-format models "once the API is designed";
the read path is designed first because search and version listing have
the simplest contract and no write-side entanglements (auth, atomicity,
Gitea) — pinning them down forces the index data model into existence
without blocking on publish design. Putting the ordering and yank
semantics in the schema package rather than leaving them as server
behaviour makes them contract, not implementation detail: a client may
rely on `versions[0]` being the newest, and on search never surfacing
yanked versions.

**Consequences**:
- Good: the publish and resolve stories build against an existing data
  model and an established pattern for where API shapes live.
- Good: yank visibility rules are written down once, in the contract.
- Bad: the server now depends on `scripticus-schema`, so D29's release
  ordering (schema first) applies to server releases too — the release
  workflow's schema-availability gate must cover the server, and the first
  server release carrying this feature needs a new schema release on PyPI
  before it.
- Bad: request/response shapes for publish and resolution remain
  undesigned; this decision deliberately does not constrain them.

---

## D31. Index tables via `create_all` until the schema has released consumers

**Decision**: The server creates its tables with SQLAlchemy's
`create_all` (idempotent, run on first database use). No migration tool
is adopted yet. Alembic (or equivalent) is adopted at the point a schema
change would strand data someone cares about — in practice, once publish
exists and real indices are populated.

**Reason**: Before publish exists, every index database is empty or
seeded test data; a migration history for tables nobody has populated is
ceremony without users, and it would slow the data model's most
change-heavy period. The index is also re-derivable in principle (D21:
everything is a projection of stored manifests), which lowers the cost of
a drop-and-recreate during early development.

**Consequences**:
- Good: the data model can evolve freely while it is young.
- Bad: a deployment that outlives a schema change must recreate its
  database until migrations arrive; acceptable only while indices are
  disposable, so this decision has an expiry date: revisit when publish
  lands.

**Revisited when publish landed (D32)**: `create_all` stays. Publish
makes populated indices *possible*, not yet *precious* — there is no
deployment whose data would survive a reinstall today, and the index
remains re-derivable from stored manifests (D21). Alembic arrives with
the first persistent deployment, and in any case before v1.0.0.

---

## D32. Publish: pass-through Gitea auth, derived-from-archive validation, format-variant rule

> **Superseded in part by [D37](#d37-batch-publish-one-multipart-request-for-a-versions-whole-archive-set-atomic-across-the-batch).** `POST /packages` now accepts a batch of
> one or more archives in a single request, validated and committed
> atomically as a set, rather than exactly one archive per request. Everything
> else below — pass-through auth, deriving identity from the archive, no
> trusted client claims, the `library` rejection, and the format-variant rule
> governing a later *separate* publish adding a format to an
> already-published version — stands unchanged.

**Decision**: Publish is `POST /packages`, a multipart upload of one
package archive with the caller's own Gitea token in the `Authorization`
header. The server derives everything — identity, platforms, language,
dependencies, commands, the content hash — from the extracted archive;
no client-supplied claim is trusted (D8). The token is used pass-through:
the index service authenticates the caller against Gitea, checks publish
permission live (the namespace is the caller or an organisation the
caller belongs to — D24), and writes the blob to Gitea's generic package
registry as the caller, holding no credentials of its own. Order of
operations is the atomicity guarantee: validate everything, upload the
blob, and commit the index record only after Gitea confirms; a commit
failure after upload triggers a best-effort blob delete. Versions are
immutable, with one carve-out — the format-variant rule: an existing
version accepts an additional artifact only when the uploaded tree's
hash equals the recorded one (D3 makes "same content" checkable) and the
archive format is not yet present, which is exactly the shape D26's
per-format packing produces. An uploaded archive's format must match the
manifest's declared platforms (a `.zip` carries Windows targets), the
`library` namespace is rejected (reserved, D5), and only the response
model (`scripticus_schema.publish_api.PublishResult`) is contract — the
request is the archive itself.

**Reason**: Pass-through auth is the smallest design that satisfies D24:
permission truth stays in Gitea, checked live, and the index service
cannot leak or misuse credentials it never holds. Deriving everything
server-side removes the entire class of index-says-X-package-says-Y
inconsistencies at the door (D21's authority rule applied to ingest).
The blob-then-record ordering means the worst crash outcome is an
orphaned blob in Gitea — invisible to resolution — rather than an index
record pointing at nothing, which would break installs.

**Consequences**:
- Good: no server-held credentials, no cached ACLs, no trusted client
  claims; the failure mode hierarchy always degrades toward "publish
  rejected", never "index corrupted".
- Good: multi-format packages (D26) publish naturally as two requests
  with no coordination.
- Bad: a CI publisher currently needs a personal Gitea token; scoped
  publish tokens remain deliberately undesigned.
- Bad: an orphaned blob is possible if the best-effort delete also
  fails; harmless but needs manual cleanup in Gitea's UI.

---

## D33. Publish-time dependency rules: targets must exist; cycles rejected

**Decision**: A declared package dependency must be a fully namespaced
`namespace/name` reference to a package already present in the index
with at least one version (the crates.io rule). Publish also rejects a
version whose dependencies would make the publishing package reachable
from itself in the package-level dependency graph (the union of every
version's declared dependencies).

**Reason**: Requiring dependencies to exist catches typos and
unpublished-yet mistakes at the moment the author can fix them, and it
makes forward references — the raw material of cycles — impossible to
mint accidentally. The cycle check is at package granularity because
that is the granularity resolution installs at (single-version-per-
closure): a cycle at package level is unresolvable regardless of which
versions ranges later select. Union-of-versions edges deliberately
over-approximate — a false rejection is a clear error at publish time,
whereas a cycle admitted into the index surfaces as a confusing
resolution failure for some innocent user later.

**Consequences**:
- Good: the resolver can assume an acyclic package graph.
- Good: dangling dependency references cannot enter the index.
- Bad: publishing mutually-referencing packages for the first time
  requires an order (publish the dependency-free one first) — same as
  crates.io, and inherent to the no-forward-references rule.
- Bad: the union-graph over-approximation can reject a publish whose
  actual version-level ranges would have been satisfiable; accepted as
  the safer failure direction.

---

## D34. `login` stores a Gitea token per remote, cargo-style; verification at login deferred

**Decision**: `scripticus login` prompts for a Gitea personal access
token (created by the user in Gitea's own settings, with package-write
scope) and stores it verbatim in `~/.scripticus/credentials.toml`, keyed
by the remote's index-service URL, with file permissions 0600. (D35
pins down the exact command grammar — remotes are named, and login
takes a remote name — and how a remote's URL is established.) No username is stored — Gitea accepts
bare token auth, and publish simply replays the stored token in the
`Authorization` header (D32's pass-through). The `SCRIPTICUS_TOKEN`
environment variable, when set, overrides the stored token — the CI
path. Credentials live in their own file, never in `config.toml`,
because D12 makes `config.toml` org-distributable via git: tokens must
not travel with it. Login stores the token without verifying it; a
verification step — a small whoami endpoint on the index service that
passes the token through to Gitea, in D24's live-check pattern — is
planned follow-up work, recorded here so the gap reads as sequencing,
not oversight.

**Reason**: This is the model every comparable tool converged on —
`cargo login` (plaintext `credentials.toml`), `npm login` (`.npmrc`),
`docker login` (unencrypted base64 in `config.json`). Plaintext on disk
with tight permissions is the accepted precedent and honest about the
threat model: an attacker who can read the file can read SSH keys too.
The token is minted and scoped by Gitea, so D32's
no-server-held-credentials property and the deliberate non-design of
Scripticus-minted token scoping both survive untouched. Per-remote
keying matches D10's multi-remote model. Deferring verification is safe
because publish must handle stale or revoked tokens with an actionable
"re-run `scripticus login`" error regardless — an unverified mistyped
token degrades to that same clear failure at first publish.

**Consequences**:
- Good: D32 is unchanged — the index service still holds no credentials
  of its own.
- Good: CI publishing works today (a personal token in a CI secret,
  supplied via `SCRIPTICUS_TOKEN`) without designing scoped tokens.
- Good: the credentials/config file split makes leaking a token through
  `config install` (D12) structurally impossible.
- Bad: a plaintext token on disk; OS-keyring integration is possible
  later but not designed.
- Bad: until the verification follow-up lands, a mistyped token
  surfaces at first publish rather than at login.

---

## D35. Named ordered remotes (`[[remotes]]`); publish defaults to the first; `login` doubles as first-time remote registration

**Decision**: `config.toml`'s remotes list (D10) is a TOML array of
tables, `[[remotes]]`, each entry `{ name, url }`. Array order remains
search-path priority (D5) — this is unchanged, just given a concrete
syntax. `scripticus publish` publishes to the first configured remote
unless `--remote <name>` names another; there is no separate
`default_remote` setting — list order alone decides, so there is only
one place priority is expressed. Login takes a remote name:
`scripticus login <name>` looks `<name>` up in the configured remotes
and prompts for a token, storing it in `credentials.toml` (D34) keyed
by that remote's URL. If `<name>` is not yet configured, this form
fails with an error naming the two-argument alternative.
`scripticus login <name> <url>` performs the same token capture but
first adds `{name, url}` to `config.toml`'s remotes list (appended, so
it takes the lowest search priority) if `<name>` is not already
present — establishing the remote and authenticating in one command for
a first-time login. If `<name>` already exists with a *different* URL,
login refuses outright (no config or credential change) rather than
silently repointing an existing remote; if it exists with the *same*
URL, the URL argument is accepted as redundant confirmation and login
proceeds as the one-argument form would.

**Reason**: an ordered array-of-tables is the only TOML shape that
keeps D5's search-path priority expressible without a second ordering
field — a bare `[remotes]` table of `name = url` pairs, or a
`default: true` flag per entry, both need extra state to say what
order-of-list already says for free. Defaulting publish to the first
remote avoids a second "which one is default" knob duplicating that
same list order; `--remote` covers every case where the first isn't
wanted, at zero cost when there is only one remote configured (expected
to be the common case). Making `login` double as first-time
registration avoids designing a separate `remote add` command for what
is normally a one-shot event — the name and URL are already in hand at
the point of first authenticating — while the URL-conflict refusal
stops a command named for authentication from ever silently moving a
remote's URL out from under other configuration.

**Consequences**:
- Good: one mechanism (list order) does both jobs — D5 bare-name
  search-path priority and publish's default target — so there is
  nothing to keep in sync between two settings.
- Good: onboarding a first remote is one command; no manual
  `config.toml` edit required.
- Good: URL-keyed credentials (D34) hold up even though remotes are now
  named — renaming a remote in `config.toml` doesn't orphan its stored
  token.
- Bad: `login`'s two-argument form does two jobs (register a remote,
  then authenticate to it), a mild surprise for a command whose name
  suggests only the latter; mitigated by documenting it plainly.
- Bad: because `config.toml` can be org-distributed (D12) and `login`
  can append to it, a user's local file can drift from the
  org-distributed baseline by picking up ad hoc remotes — accepted as
  no different from any other local edit; `scripticus config install`
  remains the way to reset to the org baseline.

---

## D36. `publish <path-prefix>` targets pre-built archives by name-version; stop-on-first-failure by default

> **Superseded in part by [D37](#d37-batch-publish-one-multipart-request-for-a-versions-whole-archive-set-atomic-across-the-batch).** With the server validating and
> committing a batch atomically, the client sends every matched archive in
> one request instead of one request per archive — there is no partial
> success left to stop-before or continue-past, so `--continue-on-error`
> and the stop-on-first-failure default described below no longer apply.
> Everything else — the `<path-prefix>` argument shape, structured
> filename matching, dash/underscore normalisation, and `--remote` — stands
> unchanged.

**Decision**: `publish` does not invoke `pack`; the two are separate
steps. `publish` takes one positional argument, a path whose final
component is a `<name>-<version>` prefix — e.g.
`scripticus publish some/dir/my-cool-script-0.1.2` — and it operates on
every archive in that directory whose D26 wheel-style filename
(`<name>-<version>-<platform-tag>-<lang>.<ext>`, name/version dashes
normalised to underscores per D26) has a `name` field and `version`
field matching exactly. Matching parses the filename into its
structured fields and compares those, not a raw string prefix — a raw
`startswith()` would wrongly match `my-cool-script-0.1.20` against a
`...-0.1.2` prefix, or misattribute a hyphenated pre-release version
into the wrong field. The path argument's name component is given in
its canonical dashed form (matching the manifest and scaffold, e.g.
`my-cool-script`); the matcher normalises both sides (dash and
underscore treated as equivalent) before comparing, so the user never
has to type or think about the filename's underscore mangling. Archives are published in a deterministic order
(sorted by filename). By default, the first archive to fail aborts the
remaining ones and the whole command exits non-zero, even if an earlier
archive in the set already succeeded — `--continue-on-error` attempts
every matched archive regardless of earlier failures, reports every
outcome, and still exits non-zero if any failed. Failure output names
which archives succeeded and which didn't, and points at re-running
`publish` to retry.

**Reason**: Requiring `pack` to run first keeps each command doing one
job and matches D26's model of archives as the independent artifacts
publish operates on, rather than publish reaching back into source
directories and manifests itself. The `<name>-<version>` path argument
reuses exactly the identifier `pack` already prints/creates, so nothing
new needs to be memorised or typed by hand. Stop-on-first-failure is
the closest a client can get to "one fails, they all fail": D16 makes
published versions immutable with no hard delete, so there is no way to
actually undo an archive that already landed in the index — the
command can only stop making things worse and refuse to report success.
That earlier success is not wasted, though: D32's format-variant rule
(same content hash, format not yet present) makes a bare re-run of
`publish` safe and idempotent, silently skipping the already-published
archive and retrying only the missing one — the client's job is just to
say that clearly. A boolean `--continue-on-error` was chosen over an
enum like D18's `--force=` because there are only two behaviours here
(stop vs. don't); D18's enum shape earns its indirection from having
several forward-relevant modes, which doesn't apply here.

**Consequences**:
- Good: `publish`'s only input concern is "which archives", entirely
  decoupled from how they were produced — consistent with D32's
  per-archive independence and the format-variant rule's existence.
- Good: the default behaviour never reports "published" when part of a
  multi-format set failed, so a user cannot walk away thinking a
  package is fully available when it isn't.
- Good: recovery is a plain re-run, no new command or state needed,
  because D32 already made retrying the missing format idempotent.
- Bad: "stop-on-first-failure" is not true atomicity and cannot be —
  documentation must be explicit that an already-succeeded archive
  stays published even when the command as a whole reports failure.
- Bad: filename-based matching depends on D26's naming convention
  staying stable; a future change to that scheme would need this
  matcher updated in step.

---

## D37. Batch publish: one multipart request for a version's whole archive set, atomic across the batch

**Decision**: `POST /packages` accepts a multipart request carrying one
or more archives — one file part each — rather than exactly one (D32).
The server stages and validates every archive in the batch (manifest
load, platform/format-group match, D33's dependency-target and cycle
checks) before writing anything to Gitea. D33's checks run once per
batch rather than once per archive: the format-variant rule already
requires every archive in a version's set to share the same content
hash, which guarantees an identical manifest across the batch, so there
is only one dependency graph to check. If every archive validates, the
server uploads each blob to Gitea and only then commits the index
record(s); if any archive in the batch fails validation, the whole
request is rejected — no blob is uploaded, nothing is committed — with
a response identifying which archive(s) failed and why. On success,
`PublishResult` reports the full batch: `artifact` becomes `artifacts`,
a list, since success means every archive in the request published
together, never a subset. Client-side, `scripticus publish
<path-prefix>` (D36) sends every archive it discovers matching the
path-prefix in this one batched request instead of looping over them
individually; the command reports the whole batch published or the
whole batch rejected, with nothing in between — D36's
stop-on-first-failure default and `--continue-on-error` flag are
dropped, since there is no partial success left to stop before or
continue past. What is unchanged from D32: pass-through Gitea auth,
deriving everything from the archive with no trusted client claims, the
`library` namespace rejection, format-must-match-declared-platforms,
and the format-variant rule governing a *later, separate* publish that
adds a format to an already-published version — that remains a batch of
size one, validated against already-committed index state exactly as
D32 specified, because those archives were not built and submitted
together.

**Reason**: The server already stages one archive in a
`tempfile.TemporaryDirectory` to validate it before touching Gitea (the
existing `publish.py` implementation); extending that to a batch of N
archives within one request's lifetime is a small generalisation of
already-built machinery, not new architecture — no persistent staging
directory, no session or multi-request transaction protocol, nothing
that outlives the request. Real atomicity across a version's format set
removes a gap D36 could only manage, not close: a client-side
stop-on-first-failure loop could never undo an archive that had already
landed via an earlier successful request, because D16 makes published
versions immutable with no hard delete — the client could refuse to
*claim* success but could not prevent the partial state existing.
Moving the atomicity boundary to "everything the client meant to
publish together" removes that gap at its source rather than
papering over it with careful failure messages.

**Consequences**:
- Good: a multi-format publish is either fully live or not live at
  all — no more "one variant published, the other didn't, re-run to
  finish" state for a client to explain to a user.
- Good: D33's checks move from "only run for the first archive of a new
  version" (today's incidental behaviour, since `existing is not None`
  skips them for a second, separate archive) to "run once per batch,
  deliberately" — the same one-check-per-manifest shape, now the
  designed behaviour rather than a side effect of request ordering.
- Good: D36 sheds an entire flag and a whole failure-reporting
  design (`--continue-on-error`, stop-on-first-failure ordering); the
  client's job shrinks to "find the matching archives, send them all,
  report the one outcome."
- Bad: this revises a shipped, already-implemented endpoint
  (`POST /packages` in `publish.py`) and its response schema
  (`PublishResult` in `publish_api.py`), including the existing test
  suite (`test_publish.py`) — not a greenfield addition.
- Bad: larger request bodies and no partial-progress salvage on a
  flaky connection — a mid-upload network failure now costs the whole
  batch, not just the one format still uploading; accepted given these
  are small script archives.
- Bad: the API surface is slightly more complex on both sides (list of
  files in, list of artifacts or one batch-level error out) than the
  single-file/single-artifact shape it replaces.

---

## D38. Three-tier command shims: a guaranteed-unique tier, convenience tiers, and no `run` command

**Decision**: Installing a package creates three shims per command in
D11's single bin directory: fully-qualified
`<namespace>.<package>.<command>`, namespaced `<namespace>.<command>`,
and bare `<command>`. The fully-qualified tier is structurally
collision-free — (namespace, package) is unique registry-wide and
command names are unique within a package — so every installed command
is always invocable, unconditionally. The other two tiers are
conveniences and may collide; collisions at either tier follow D11's
last-install-wins rule and are surfaced and gated exactly as bare-shim
conflicts are today (D17's transaction summary, D18's force semantics,
D28's replacement picker on uninstall). `use` re-points a convenience
shim by name, the name's dot count selecting the tier (`use foo/d b`
re-points bare `b`; `use foo/d foo.b` re-points `foo.b`, and only to a
package in that namespace); the fully-qualified tier is never
re-pointable — it always means the one thing it names. Convenience
shims point directly at the fully-qualified shim, never at each other:
inspecting any shim reveals the true owner in one hop, and re-pointing
one tier never changes another. Tier membership is recoverable from the
name alone because namespaces, package names, and command names all
exclude `.`: one dot-free segment is always a bare command, two
segments always `<namespace>.<command>`, three always fully qualified —
no name in one tier can exist in another. (Windows shims remain
generated `.cmd` files; `foo.b` resolves to `foo.b.cmd` via PATHEXT as
usual.) The planned `scripticus run` command is dropped: D11's
"namespaced invocation always available" becomes a property of the bin
directory rather than a subcommand, and an npx-style ephemeral
fetch-and-execute was never the intent. The post-v1
dot-qualified-invocation roadmap item is pulled into v1 scope in a
stronger form — that sketch imagined only `<namespace>.<command>`,
which two same-namespace packages providing the same command could
still contest.

**Reason**: A `run` command is a subprocess wrapper, and a wrapper must
re-implement what the operating system already does for any file on
PATH — argument passing, exit-code propagation, stdin/TTY inheritance,
signal forwarding — each with POSIX and Windows variants. Shims get all
of that for free, so the always-invocable guarantee moves from code
that must be written and maintained to a naming convention the
installer materialises. The pattern has strong precedent: `python3.11`
alongside `python3` — every install drops its fully-qualified name
while a bare convenience points at the current winner. Three tiers
rather than two because `<namespace>.<command>` alone cannot carry the
guarantee: command names are unique only within a package, so two
packages in one namespace can both provide `build` and contest
`foo.build`. With a fully-qualified tier underneath, both convenience
tiers can be treated identically — collide, warn, re-point — giving one
collision rule everywhere instead of a special case per tier. The dot
was reserved for exactly this when the identifier character sets were
pinned down: `.` is excluded from namespaces, package names, and
command names precisely so dotted composition parses unambiguously.

**Consequences**:
- Good: every installed command is always invocable; the guarantee is
  structural, with no invocation code path to write or test.
- Good: one collision rule at every tier — the D17/D18/D28 machinery
  generalises from "the bare shim" to "any convenience shim" instead of
  growing a parallel mechanism.
- Good: ownership is readable from the filesystem — any convenience
  shim names its fully-qualified target one hop away.
- Good: `run` is never built; the CLI surface shrinks by one command
  whose grammar (multi-command packages, installed-only vs
  fetch-and-run) was never settled.
- Bad: three shims per command make `ls ~/.scripticus/bin` noisier.
- Bad: lockfile ownership bookkeeping changes shape (per-tier shim
  ownership rather than a flat command list) and the shipped
  install/uninstall/use code paths must all be revised — this lands on
  implemented code, not greenfield.
- Bad: a default-entrypoint package `foo/a` yields the ugly `foo.a.a` —
  accepted; it is typed only when both convenience tiers are contested,
  which is exactly when its existence matters.
