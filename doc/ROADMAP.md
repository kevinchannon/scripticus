# Roadmap

## v1.0.0 — Internal release

All items below are in scope for v1.0.0. The target deployment is a single
organisation (internal use), with trust provided by organisational access
control rather than cryptographic assurance.

### Package model

- [x] Package unit is a directory, distributed as a compressed archive
      (`.tar.gz` on POSIX/macOS, `.zip` on Windows).
- [x] TOML manifest at the package root declaring: namespace, name, version,
      language, supported platforms (OS, optionally narrower distro list),
      required and optional system tools, package dependencies, and commands.
- [x] Package names are kebab-case (lower-case with dashes), enforced at
      publish time. Script filenames inside a package follow language/platform
      conventions and are not constrained by the package naming rule.
- [x] Entrypoint rules:
  - No `[commands]` table → `src` MUST contain `main.<ext>` (extension per
    language convention); this runs when the package name is typed.
  - `[commands]` table present → each entry maps a command name to a script
    path (`cmd-name = "src/script-name"`); every listed command gets a shim.
- [x] Standard layout: manifest at top level, `src/` for scripts, `test/` for
      tests, `LICENSE`, `README.md`.
- [x] Strict semver enforced at publish; non-conforming versions rejected.
- [x] Multiple platform/language variants of the same package version may
      coexist as separate artifacts. Artifact filenames encode
      name/version/platform/language tags (wheel-style; dashes in name/version
      normalised to underscores in the filename so the dash remains an
      unambiguous field separator). The manifest, not the filename, is the
      source of truth for resolution.

### Identity, namespacing & integrity

- [x] Content-addressed artifact identity from day one: the canonical
      reference for an artifact is a hash of the package directory tree
      (Merkle-style, as git does for trees).
- [x] Fully namespaced packages (`owner/name`, GitHub-style). No flat tier.
- [x] Namespace allocation is first-come-first-served and maps 1:1 onto the
      backing registry's (Gitea's) user/org namespace and ACLs.
- [x] Namespace character set: lower-case letters, digits, and dashes,
      beginning with a letter (validated client-side in `new`/`pack`;
      enforced authoritatively at publish). Stricter than Gitea's own
      username rules, deliberately: `_` is excluded so that in artifact
      filenames underscores unambiguously mark normalised dashes *within* a
      field while dashes delineate the fields themselves
      (name/version/platforms/language — as Python wheels do), and `.` is
      excluded to keep it free for dot-qualified invocation of namespaced
      command overloads post-v1 (see below).
- [x] The `library` namespace is reserved for future curated/reviewed
      packages.
- [x] No framework-level manifest correctness checks (no lint, no sandboxed
      verification). Manifest accuracy is entirely the package developer's
      responsibility; this is stated explicitly in documentation.

### Server (index service)

- [x] Python service (FastAPI) fronting a Gitea instance used as the
      storage/auth/namespace substrate (generic package registry).
- [x] Owns the package index: manifest-aware search (name, tags, platform,
      language), version listing, and resolution.
- [x] Batch atomic publish: the client sends one or more archives — a
      version's whole format-group set — plus manifests to the index
      service in a single request; the service validates every archive
      (without trusting the client) before writing any blob to Gitea, and
      commits the index record(s) only after Gitea confirms every write. A
      failure anywhere in the batch rejects the whole request — nothing
      uploaded, nothing committed (D37). Duplicate versions rejected.
- [x] Server-side dependency resolution (D42): `POST /resolve`
      takes a root package plus the client's platform and installed
      closure, and returns the full resolved transitive closure as a flat
      list of (package, version, content hash, download pointer,
      direct/transitive, command map — D47) plus aggregated tool
      requirements. Single-version-per-closure (no side-by-side versions);
      the installed closure enters as hard constraints so resolution
      neither breaks nor needlessly bumps installed packages. An
      unsatisfiable window is a hard error.
- [x] Cycle detection at publish time.
- [x] Token-verification endpoint: `GET /whoami`, a whoami-style
      pass-through of the caller's Gitea token, so the client can verify a
      token at `login` time rather than at first publish (D40, follow-up
      to D34). `login` now calls it to verify before storing (D41).
- [x] Platform-aware resolution (D42): the client's platform is
      an input to `/resolve` so the correct artifact variant is selected
      automatically.
- [x] Tool-dependency resolution (D43/D44): the server
      aggregates each tool requirement over the closure; the client checks
      PATH presence and installs the missing set by shelling out to an
      operator-configured `[tools] install` command (no package-manager
      logic encoded). Scripticus itself never needs privilege — its state
      is user-space; only the tool command elevates, via an optional
      machine-set `[tools] escalate` prefix (`sudo`/`doas`/empty). Missing
      required tools with no installer configured abort with a
      `--skip-tools` escape. v1 is name-only;
      versioned tool windows are a fast-follow needing a manifest/schema
      extension.
- [x] Read path (D42): `/resolve` returns metadata plus direct
      Gitea download pointers (relative to the front URL, D45); the client
      fetches blobs from Gitea itself with its stored token, staging and
      hash-verifying all blobs before committing (no companion download
      endpoint, per D9).
- [ ] npm-style yank: yanked versions are excluded from `latest`/search
      resolution but remain fetchable when directly pinned (including via
      lockfiles). No hard delete.
- [x] Data model: relational schema (namespace → package → package_version →
      artifact/dependency/tool_dep/command), storing each manifest verbatim
      alongside publish-time-extracted queryable columns (blob authoritative,
      columns a re-derivable projection). Dependency graph as plain rows
      resolved on demand; yank as a whole-version flag; nothing ACL-shaped
      cached from Gitea (live permission checks at publish); no
      install/download tracking.
- [x] SQLite via SQLAlchemy (no SQLite-isms), keeping Postgres as a
      configuration change for larger deployments.
- [x] Deployment as a single `docker-compose.yml` bundling Gitea + the index
      service; SQLite-backed Gitea acceptable for small deployments.
- [x] Reverse proxy in the compose bundle presenting one user-facing URL,
      routing internally to the index service and Gitea (D45), so clients
      and enterprise firewalls see a single endpoint and download pointers
      stay relative to it.

### Client (CLI)

The command surface at a glance (details in the checklist below and usage
docs in the client README):

| Command                     | Purpose                                                | Status      |
| --------------------------- | ------------------------------------------------------ | ----------- |
| `new <lang> <name> -n <ns>` | Scaffold a package directory                           | Implemented |
| `pack <dir> [-o <dir>]`     | Archive a package into distributable artifacts         | Implemented |
| `install -f <archive>`      | Install from a local archive                           | Implemented |
| `uninstall <pkg>`           | Remove a package's files and shims                     | Implemented |
| `use <pkg> <command>`       | Re-point a command shim at an installed package        | Implemented |
| `login <name> [<url>]`      | Store a Gitea token; register a remote first time      | Implemented |
| `publish <path-prefix>`     | Publish packed archives to a remote, as one batch      | Implemented |
| `install <ns/name>[@ver]`   | Install from a remote, with dependency resolution      | Implemented |
| `search <query>`            | Search remotes by content (name, description, commands) | Implemented |
| `list [glob]`               | List installed + available packages, glob-filtered     | Implemented |
| `update [<pkg>]`            | Update installed remote-provenance packages            | Planned     |
| `init`                      | Post-install bootstrap: PATH entry + state skeleton    | Implemented |
| `config install <git-url>`  | Pull org-distributed client configuration              | Planned     |
| `yank <ns/name>@<ver>`      | Hide a published version from search/latest            | Planned     |

There is deliberately no `run` command: D38's three-tier shims make every
installed command directly invocable by its namespaced names instead.

- [x] `pack <dir> [-o <dir>]`: validate the manifest, then archive the
      package directory with wheel-style filename tags — one archive per
      format the declared targets call for (`.tar.gz` for POSIX/macOS,
      `.zip` for Windows; both when both are targeted, per D26).
- [x] `install <ns/name>[@version]` from a remote (D46): resolves against
      the configured remotes in priority order (first hosting the root; the
      closure is single-remote by D33), `--remote` to force one. Calls
      `/resolve` with the installed closure, plans (new installs, version
      changes, shim + tool conflicts) via the D17 transaction flow, then
      fetches/verifies/installs (D42/D43/D45); tools install first, then all
      blobs stage-and-verify before any unpack. v1 requires the
      fully-namespaced form; bare-name resolution via a user-configurable
      namespace search path (Homebrew-tap-style, D5) is deferred — bare
      names are purely a client-side convenience over always-namespaced
      identities.
- [x] `install -f|--file <archive>` for local installs (pip-style). Install
      state records provenance (remote vs local file); `update` skips/warns on
      local-provenance packages.
- [x] dnf/apt-style install confirmation flow: fully resolve first, then show
      (a) what is newly installed / version-changed (downgrades called out
      distinctly; already-satisfied dependencies not listed as actions),
      (b) shim conflicts, shown distinctly and naming the namespaced package
      that currently owns each affected shim, then (c) prompt.
- [x] `--force=no-conflicts` (what bare `-y` means): auto-accept
      new installs, but abort the whole transaction (nothing installed,
      non-zero exit) on any shim conflict. `--force=all`: auto-accept
      everything, but log every overwritten shim. Interactive mode is
      accept-whole-transaction-or-abort (no per-item selection).
- [x] Shim scheme: single `~/.scripticus/bin` directory added to PATH once at
      Scripticus install time. POSIX: symlink or one-line wrapper. Windows:
      generated `.cmd` shim invoking the correct interpreter (no compiled
      shims needed).
- [x] `init`: the one-shot post-install bootstrap the shim scheme's
      "added to PATH once at install time" premise relies on — pip cannot
      edit a shell profile, so a command must: idempotently add
      `~/.scripticus/bin` to the persistent PATH (shell profile on POSIX,
      the user PATH on Windows, D39), pre-create the client state skeleton
      so the PATH entry isn't dangling, and tell the user to restart their
      shell.
- [x] Command-name collisions: last-install-wins, with `use` to manually
      re-point a shim.
- [x] Three-tier shims (D38): every command materialises a
      guaranteed-unique `<ns>.<pkg>.<cmd>` shim plus `<ns>.<cmd>` and bare
      convenience pointers (which target the fully-qualified shim
      directly). Convenience-tier collisions follow the same
      last-install-wins/`use`/conflict-surfacing rules as bare shims do
      today; the fully-qualified tier never collides, so every installed
      command is always invocable — which is why there is no `run`
      command.
- [x] Local install-state file (lockfile-style): installed packages, resolved
      versions and hashes, full resolved closure with direct-vs-transitive
      marking, and provenance (remote vs local `-f`). A remote install
      records the whole resolved closure; later resolves send the
      remote-provenance entries as installed identities (D42).
- [x] Client config: remotes list as an ordered `[[remotes]]` array of
      `{ name, url }` tables (doubling as the namespace search path; order
      is also `publish`'s default-remote priority, D35) and install state.
      No Conan-style profiles, no separate `default_remote` setting.
- [x] `publish <path-prefix>` (e.g. `some/dir/my-cool-script-0.1.2`):
      publish every pre-built archive at that location whose D26
      wheel-style filename's name/version fields match exactly (not a raw
      string prefix; dash/underscore normalised so the canonical dashed
      name matches the filename's mangled form), sending them all as one
      batched request — published together or rejected together, no
      partial-success state (D36/D37). `publish` never invokes `pack`
      itself. `--remote <name>` to target a non-default configured remote.
- [x] `login <name>` (existing remote) / `login <name> <url>` (first-time
      login, also registers the remote in `config.toml`, D35): store a
      Gitea personal access token per remote in `credentials.toml`
      (plaintext, 0600, cargo-style; a separate file from the
      org-distributable `config.toml`), with `SCRIPTICUS_TOKEN` as the CI
      override (D34). The token is verified against the remote's `/whoami`
      before being stored, reporting the authenticated identity (D40/D41).
- [x] `search <query>` (D48/D49): call every configured remote's `/search`
      in priority order and merge the hits, each tagged with its remote —
      fan-out, not first-match-wins like `install`. Matches package *content*
      (name, description, command names, case-insensitively; tags deferred)
      plus optional `--platform`/`--language` filters. `--remote` restricts to
      one. Best-effort: a down/erroring remote is a warning, only an
      all-remotes failure is fatal; the call is anonymous (no token).
- [x] `list [glob]` (D49): dnf-style enumeration over *identity* — a shell
      glob over `namespace/name`, showing an Installed section (local
      lockfile) and an Available section (remotes' catalog minus what's
      installed). `--installed` (offline) / `--available` restrict; `--remote`
      picks the registry. Complements `search`'s content match; gives
      Scripticus its first installed-listing.
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
- [ ] Federation/promotion between internal and public indices — including
      cross-remote dependency closures (v1 keeps each closure single-remote,
      D33/D46; a shared dep on another remote must be mirrored until then).
      Layers onto D42's resolver as client-side orchestration or
      server-to-server resolution, not a rework of it.
- [ ] Bare-name resolution via the D5 namespace search path: settle the
      config shape (how a namespace maps to a remote) deferred by D46, so
      `install foo` works without the full `namespace/foo`.
- [ ] OS-keyring storage for login credentials (Secret Service / Keychain /
      Credential Locker), replacing the plaintext `credentials.toml` at rest
      where a keyring is available, with the file kept as the headless/CI
      fallback (hardening on D34's storage model).
- [ ] Richer tool configuration (beyond v1's single `[tools] install`
      command, D44): an optional query/check command to verify a tool
      satisfies a version window rather than mere PATH presence, versioned
      tool windows (with the manifest/schema extension D43 defers), and
      per-OS command variants for mixed-fleet org-distributed config.
- ~~Dot-qualified invocation of namespaced command overloads~~ — promoted
      into v1.0.0 scope by D38 (three-tier shims), in a stronger form than
      sketched here: a guaranteed-unique `<namespace>.<package>.<command>`
      tier underlies the `<namespace>.<command>` convenience, closing the
      same-namespace collision case this item's two-segment form left open.
