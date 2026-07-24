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

## Snippets — reusable boilerplate, copy-pasted not run

Distribute *boilerplate*: the fiddly-but-standard code every language has and
nobody remembers exactly — argument parsing (shell's switch/case + `shift`
dance, or another language's equivalent) and signal `trap`s are the motivating
pair. A snippet is printed to the terminal; you read it, paste it, and edit it
into your own script. The design is settled (to be recorded as D58);
implementation is unscheduled.

Distinct from libraries (D57), and the distinction is the whole justification:

- A **library** has fixed content and a *live* relationship to the consumer —
  sourced at runtime, versioned, resolved, updatable. A **snippet** has a
  reusable *shape* but per-script content — you read and edit it. Sourcing fails
  for arg-parsing and traps precisely because which flags, which signals, and
  what cleanup are inherently per-script; the boilerplate *is* the deliverable.
- D57's shell-only rationale does **not** carry over. That argument was "other
  languages already have package managers" — but PyPI hands you no `argparse`
  skeleton. Snippets are multi-language from day one.

Rides on existing design for free:

- **Content-addressed identity (D3)**, **publish / yank / update**, and
  **search** are blob-and-index mechanics — a snippet package is just a package
  tree, unchanged.
- **No shims, no PATH, no staging.** A snippet is never installed onto a path or
  run; `snip` reads a file at a lockfile-known location. It sidesteps the
  D11/D38 shim system entirely.

New surface:

- [ ] **Manifest marker + structure.** A `[snippet]`-family package is a third
      package kind, **mutually exclusive with `[commands]` and `[library]`**
      (D57 symmetry). Each snippet is a `[snippet.<name>]` section carrying only
      a `description`; the code lives in files under `src/` by convention —
      `src/<name>.<ext>`, flat, no extra directory layer, so authors keep
      shellcheck / syntax highlighting / `-n` on real source files rather than
      strings embedded in TOML.
- [ ] **Arbitrarily multi-language, per-file.** A single snippet package may hold
      the same snippet name in many languages as sibling files — `src/args.py`,
      `src/args.cpp`, `src/args.sh` — all sharing one `[snippet.args]` section
      (the description is language-agnostic intent). Consequently a snippet
      package has **no package-level `language` field** (unlike command/library,
      where it is load-bearing for execution/sourcing). A snippet's language is a
      pure *label* — Scripticus never runs it — so it needs no interpreter and no
      `LANGUAGES`-table entry: snippets reach languages the rest of the system
      structurally cannot (C++, Rust, Go, Java) with zero table changes.
- [ ] **The `snip` command (terseness is the governing constraint).** `snip`
      competes with the user's muscle memory and a web-search tab, not with other
      package managers; if it is not faster than retyping the boilerplate, it is
      unused. So the everyday form is `snip <name>.<ext>` → the snippet on
      stdout. `snip <name>` (no extension) collapses to that when only one
      variant exists, and otherwise **lists the available variants** rather than
      guessing (there is no meaningful "last wins" across languages a dev uses in
      parallel). Fully-namespaced `ns/name:<name>.<ext>` is the rarely-touched
      escape hatch (v1's fully-namespaced constraint, D46, applies unchanged).
- [ ] **Stdout only — no in-file insertion.** `snip` is a pure read with no side
      effects and never mutates a file it does not own. Composition is the shell's
      job: `snip trap.sh >> script.sh`, `:r !snip trap.sh` in vim, `pbcopy`, etc.
- [ ] **One collision axis.** Folding the language into the name token
      (`args.cpp`) leaves only the *namespace* collision — two same-language packs
      both defining `args.cpp` — which reuses D38 last-install-wins + `use`, not a
      new mechanism. Because reads are cheap and side-effect-free, the tie-break
      can lean toward listing over silently guessing.
- [ ] **Derived language enumeration (author maintains nothing).** The author
      never hand-lists a snippet's languages. The filename → language-label rule
      is a pure `common` function (D51). It is **projected server-side at publish
      into the index** — not written back into the manifest — keeping the manifest
      verbatim authored intent (name + description) and the derived variant list a
      re-derivable projection (D21). The server already fully extracts every
      archive at publish (`_extract_archive` / `tree_hash` in `publish.py`), so
      globbing `src/` for `<name>.*` adds no new machinery. The client's local
      `snip` globs its own installed `src/`; remote `search` reads the projection
      — so **languages influence search results**.
- [ ] **Scaffolding.** `scripticus new --snippet` (alongside D57's
      `--cmd`/`--lib`) so the multi-file tree costs one command and authors keep
      their linters. Ceremony is a scaffolding problem, not a format problem.
- [ ] **Left to implementation.** Whether a description may be overridden
      per-language variant (default: shared); whether the `snip` *invocation*
      earns further terseness at the CLI-prefix level (a shipped short alias, or
      `init` (D39) encouraging one); extension aliasing (`.cc` vs `.cpp` is
      WYSIWYG — the on-disk extension is the typed key, no normalization for v1);
      and how snippet packages surface in `list`.

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
