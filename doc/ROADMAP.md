# Roadmap

v1.0.0 — the internal release — is complete and shipped; its scope is preserved
in [archive/ROADMAP-v1.0.0.md](archive/ROADMAP-v1.0.0.md). This roadmap tracks
work beyond v1. Nothing here is scheduled; it is recorded so that v1 decisions
do not preclude it.

## Library scripts — reusable, non-runnable shell code

Distribute code that is *included into* runnable scripts rather than run
directly: reusable shell functions and fragments. The design is settled (to be
recorded as D57); implementation is unscheduled.

Scope and rationale:

- **Shell only.** Libraries target the POSIX-`source` shell family: `sh` (the
  portable baseline) and `bash` (an opt-in superset). Every other scripting
  language Scripticus distributes *commands* in — Python, PowerShell, Ruby,
  Perl, etc. — already has a mature library-distribution story (pip/PyPI,
  PowerShellGet/PSGallery, gem, CPAN), so library-grade reuse in those
  languages is a **deliberate non-goal**: Scripticus points authors at the
  language's own package manager. Shell is the one common scripting language
  with no native answer — the gap this fills, and (not coincidentally) the only
  language needing a Scripticus-authored loader.
- Consistent with **D14**: Scripticus never verifies that a library is actually
  sourceable or actually used. The manifest declares intent, the client plumbs,
  correctness is the author's problem.

Rides on existing design for free:

- **Content-addressed identity (D3)** is agnostic to runnability — a library is
  just a package tree.
- **Resolution (D42/D47)** already fits: a command depending on a library is an
  ordinary package dependency, and single-version-per-closure is exactly the
  semantics a library wants (you cannot sanely source two versions of one
  library into a namespace).
- **Publish / yank / update** are blob-and-index mechanics, unchanged.

New surface:

- [ ] **Manifest marker.** A `[library]` table, present with no fields, marks a
      package as a library; it is **mutually exclusive with `[commands]`**. A
      library's language must be `sh` or `bash`.
- [ ] **Package structure (BATS-style convention).** The sourced entry point is
      `src/load.<ext>` — analogous to the `src/main.<ext>` command default. The
      `load` script may source siblings from its own `src/` or `scr_load` other
      library packages; the manifest enumerates nothing.
- [ ] **Scaffolding.** `scripticus new` gains a `--cmd`/`--lib` flag to choose
      what is being built: a command package (today's behaviour, the default)
      or a library package (the `[library]` marker plus a `src/load.<ext>`
      skeleton instead of `src/main.<ext>`).
- [ ] **The `sh` language.** Add `sh` to the language table as the POSIX
      baseline, distinct from `bash`; this also enables `sh` *commands*.
- [ ] **Compatibility (`common`).** A package's declared `language` doubles as
      "what it can source." A pure `language_satisfies` in `common`, shared by
      the resolver and install checks: a consumer of language `C` may load a
      library of language `L` iff `L == "sh"` or `L == C`. So `sh` libraries
      satisfy every consumer; `bash` libraries satisfy only `bash` consumers.
- [ ] **The `scr_load` loader.** Written in POSIX `sh` (it is sourced into
      whatever shell the consumer runs, so it must be the most portable thing in
      the system) and referenced by fully-namespaced `namespace/name` (matching
      v1 install; bare-name convenience deferred). It searches in-process — a
      sourced function, not a subprocess — so there is no per-`source` fork.
      Behaviour: transitive loading (pulls a library's own library
      dependencies), an idempotent include-guard (re-loading within a process is
      a no-op, so diamonds are safe), and a nonzero return on a missing library
      (the caller handles it; no hard abort). No version-pinned references —
      single-version-per-closure stays inviolate, so a reference never carries a
      version.
- [ ] **Staging + availability.** Exports stage to
      `$SCRIPTICUS_LIB/<namespace>/<name>/` — a version-less path, since the
      closure pins the version. The loader is available in **two documented
      contexts**: a command Scripticus launches through its own shim gets
      `$SCRIPTICUS_LIB` and `scr_load` injected automatically; a user's own
      ad-hoc script opts in via `scripticus init`, which owns the global env
      export alongside its existing PATH bootstrap (D39).
- [ ] **Discovery.** Decide how command-less libraries surface in `search`
      (today a content match over name/description/command names) and `list`.
      Left to implementation.
- [ ] **Lifecycle.** Decide whether uninstalling a command whose library is now
      unused auto-removes the orphaned library (à la the D28/D44 reconciliation)
      or leaves it in place. Left to implementation.

> **Note:** we also intend to add an `import` command to bring an existing
> script into a Scripticus package (as an alternative to scaffolding from
> scratch with `new`). That is a separate discussion, not part of the library
> design above.

## Widening beyond a single organisation

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
- [ ] Bare-name resolution via the D5 namespace search path: settle the config
      shape (how a namespace maps to a remote) deferred by D46, so `install foo`
      works without the full `namespace/foo`.
- [ ] OS-keyring storage for login credentials (Secret Service / Keychain /
      Credential Locker), replacing the plaintext `credentials.toml` at rest
      where a keyring is available, with the file kept as the headless/CI
      fallback (hardening on D34's storage model).
- [ ] Explicit remote priority/reordering: let `config remote` express where a
      newly added remote lands in the search-path order rather than v1's
      append-only, remove-and-re-add-to-reorder (D56) — e.g. `add --first` or a
      priority index.
- [ ] Richer tool configuration (beyond v1's single `[tools] install` command,
      D44): an optional query/check command to verify a tool satisfies a version
      window rather than mere PATH presence, versioned tool windows (with the
      manifest/schema extension D43 defers), and per-OS command variants for
      mixed-fleet org-distributed config.

## Carried over from v1.0.0

The one v1-scoped item that was designed but not shipped:

- [ ] Editable/dev install (`pip install -e` equivalent): shim points at the
      working directory for iterating on the installed experience without a
      publish cycle.
