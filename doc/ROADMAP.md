# Roadmap

## v1.0.0 — Internal release

All items below are in scope for v1.0.0. The target deployment is a single
organisation (internal use), with trust provided by organisational access
control rather than cryptographic assurance.

### Package model

- [ ] Package unit is a directory, distributed as a compressed archive
      (`.tar.gz` on POSIX/macOS, `.zip` on Windows).
- [ ] TOML manifest at the package root declaring: namespace, name, version,
      language, supported platforms (OS, optionally narrower distro list),
      required and optional system tools, package dependencies, and commands.
- [ ] Package names are kebab-case (lower-case with dashes), enforced at
      publish time. Script filenames inside a package follow language/platform
      conventions and are not constrained by the package naming rule.
- [ ] Entrypoint rules:
  - No `[commands]` table → `src` MUST contain `main.<ext>` (extension per
    language convention); this runs when the package name is typed.
  - `[commands]` table present → each entry maps a command name to a script
    path (`cmd-name = "src/script-name"`); every listed command gets a shim.
- [ ] Standard layout: manifest at top level, `src/` for scripts, `test/` for
      tests, `LICENSE`, `README.md`.
- [ ] Strict semver enforced at publish; non-conforming versions rejected.
- [ ] Multiple platform/language variants of the same package version may
      coexist as separate artifacts. Artifact filenames encode
      name/version/platform/language tags (wheel-style; dashes in name/version
      normalised to underscores in the filename so the dash remains an
      unambiguous field separator). The manifest, not the filename, is the
      source of truth for resolution.

### Identity, namespacing & integrity

- [ ] Content-addressed artifact identity from day one: the canonical
      reference for an artifact is a hash of the package directory tree
      (Merkle-style, as git does for trees).
- [ ] Fully namespaced packages (`owner/name`, GitHub-style). No flat tier.
- [ ] Namespace allocation is first-come-first-served and maps 1:1 onto the
      backing registry's (Gitea's) user/org namespace and ACLs.
- [ ] The `library` namespace is reserved for future curated/reviewed
      packages.
- [ ] No framework-level manifest correctness checks (no lint, no sandboxed
      verification). Manifest accuracy is entirely the package developer's
      responsibility; this is stated explicitly in documentation.

### Server (index service)

- [ ] Python service (FastAPI) fronting a Gitea instance used as the
      storage/auth/namespace substrate (generic package registry).
- [ ] Owns the package index: manifest-aware search (name, tags, platform,
      language), version listing, and resolution.
- [ ] Single-request atomic publish: the client sends the archive + manifest
      to the index service; the service validates the manifest (without
      trusting the client), writes the blob to Gitea, and commits the index
      record only after Gitea confirms the write. Duplicate versions rejected.
- [ ] Server-side dependency resolution: given a root package, the service
      returns the full resolved transitive closure as a flat list of
      (package, version, download pointer). Single-version-per-closure
      (no side-by-side versions of the same package).
- [ ] Cycle detection at publish time.
- [ ] Platform-aware resolution: the client's platform is an input to
      resolution so the correct artifact variant is selected automatically.
- [ ] Read path: index service returns metadata plus direct download
      pointers/tokens; the client fetches blobs from Gitea itself.
- [ ] npm-style yank: yanked versions are excluded from `latest`/search
      resolution but remain fetchable when directly pinned (including via
      lockfiles). No hard delete.
- [ ] Data model: relational schema (namespace → package → package_version →
      artifact/dependency/tool_dep/command), storing each manifest verbatim
      alongside publish-time-extracted queryable columns (blob authoritative,
      columns a re-derivable projection). Dependency graph as plain rows
      resolved on demand; yank as a whole-version flag; nothing ACL-shaped
      cached from Gitea (live permission checks at publish); no
      install/download tracking.
- [ ] SQLite via SQLAlchemy (no SQLite-isms), keeping Postgres as a
      configuration change for larger deployments.
- [ ] Deployment as a single `docker-compose.yml` bundling Gitea + the index
      service; SQLite-backed Gitea acceptable for small deployments.

### Client (CLI)

- [ ] Python CLI: `search`, `install`, `update`, `uninstall`, `publish`,
      `new`, `use`, `config`.
- [ ] `install <ns/name>[@version]` with bare-name resolution via a
      user-configurable namespace search path (Homebrew-tap-style). Bare names
      are purely a client-side resolution convenience; stored identity is
      always fully namespaced.
- [ ] `install -f|--file <archive>` for local installs (pip-style). Install
      state records provenance (remote vs local file); `update` skips/warns on
      local-provenance packages.
- [ ] dnf/apt-style install confirmation flow: fully resolve first, then show
      (a) what is newly installed / version-changed (downgrades called out
      distinctly; already-satisfied dependencies not listed as actions),
      (b) shim conflicts, shown distinctly and naming the namespaced package
      that currently owns each affected shim, then (c) prompt.
- [ ] `--force=no-conflicts` (default for bare `-y`/`--force`): auto-accept
      new installs, but abort the whole transaction (nothing installed,
      non-zero exit) on any shim conflict. `--force=all`: auto-accept
      everything, but log every overwritten shim. Interactive mode is
      accept-whole-transaction-or-abort (no per-item selection).
- [ ] Shim scheme: single `~/.scripticus/bin` directory added to PATH once at
      Scripticus install time. POSIX: symlink or one-line wrapper. Windows:
      generated `.cmd` shim invoking the correct interpreter (no compiled
      shims needed).
- [ ] Command-name collisions: last-install-wins, with `use` to manually
      re-point a shim, and namespaced invocation always available to
      disambiguate.
- [ ] Local install-state file (lockfile-style): installed packages, resolved
      versions and hashes, full resolved closure with direct-vs-transitive
      marking, and provenance.
- [ ] Client config: remotes list (doubling as the namespace search path) and
      install state. No Conan-style profiles.
- [ ] `config install <git-url>` to roll out org-wide client configuration
      (remotes, defaults) in one command (Conan-style).
- [x] `new <lang> <pkg>`: scaffold directory + skeleton manifest, with
      language-appropriate entrypoint naming (e.g. `main.sh`, `main.py`,
      PascalCase for named PowerShell commands).
- [ ] Editable/dev install (`pip install -e` equivalent): shim points at the
      working directory for iterating on the installed experience without a
      publish cycle.
- [ ] Post-download content-hash verification against the resolved hash.

## Post-v1.0.0 — Widening beyond a single organisation

Not scheduled; recorded so v1 decisions do not preclude them.

- [ ] Public/multi-tenant hosting model (same client, different default
      remote/resolution configuration).
- [ ] Cryptographic assurance layer: artifact signing and verification
      (Sigstore/cosign-style), enabled by the existing content-addressed
      identity without changes to storage or reference formats.
- [ ] Provenance metadata (who built it, from what commit, via what pipeline;
      SLSA as the reference framework).
- [ ] Publish approval/review gates (policy change on the existing publish
      path, not an architecture change).
- [ ] Curated/reviewed package programme under the reserved `library`
      namespace.
- [ ] Federation/promotion between internal and public indices.
