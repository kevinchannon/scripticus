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

**Decision**: `-y`/`--force` auto-accepts the transaction but aborts it
entirely (nothing installed, non-zero exit) on any shim conflict.
`--force=all` accepts everything, logging each overwritten shim. Chosen over
apt-style all-or-nothing `-y`.

**Reason**: Shims are execution-shadowing PATH entries; silently replacing
one from a CI script is a real incident pattern (apt's `-y` has caused
exactly this). Splitting the flag keeps scripted installs safe by default
while keeping full automation available explicitly. Abort-rather-than-skip
on conflict keeps the D17 invariant that exit codes never mean "partially
installed".

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
