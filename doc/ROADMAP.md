# Roadmap

v1.0.0 — the internal release — is complete and shipped; its scope is preserved
in [archive/ROADMAP-v1.0.0.md](archive/ROADMAP-v1.0.0.md). This roadmap tracks
work beyond v1. Unless a section says otherwise, nothing here is scheduled; it
is recorded so that v1 decisions do not preclude it. Snippets (D58) and library
scripts (D57) have since shipped, and their sections are kept only as pointers.

## Library scripts — reusable, non-runnable shell code

**Shipped** (D57). Code that is `source`d into a runnable script rather than run
directly: reusable shell functions, distributed as a `[library]` package whose
`src/load.<ext>` a consumer pulls in with `scr_load <namespace>/<name>`. Shell
only — `sh` and `bash` — because every other language Scripticus distributes
commands in already has a package manager for reusable code, which is a
deliberate non-goal rather than a gap. The design is recorded in
[DECISIONS.md](DECISIONS.md) D57 (including what implementation settled: the
source-wrapper shim, the generated staging wrapper, `kind`-tag discovery, and
advisory-only orphan handling) and the mechanics in
[ARCHITECTURE.md](ARCHITECTURE.md) (package kinds, index data model,
client-side state); authoring and usage are in the
[client README](../client/README.md).

> **Note:** we still intend to add an `import` command to bring an existing
> script into a Scripticus package (as an alternative to scaffolding from
> scratch with `new`). That is a separate discussion, unaffected by the library
> work.

## Snippets — reusable boilerplate, copy-pasted not run

**Shipped** (D58). Distributing *boilerplate* — the fiddly-but-standard code
every language has and nobody remembers exactly, argument parsing and signal
`trap`s being the motivating pair — as a third package kind that is printed for
you to paste and edit, never run or sourced. A snippet has a reusable *shape*
but per-script content, which is exactly why a library (D57) cannot serve it.
The design is recorded in [DECISIONS.md](DECISIONS.md) D58 and the mechanics in
[ARCHITECTURE.md](ARCHITECTURE.md) (package kinds, index data model,
client-side state); usage and authoring are in the
[client README](../client/README.md).

## Widening beyond a single organisation

- [ ] Public/multi-tenant hosting model (same client, different default
      remote/resolution configuration).
- [ ] Distribute `get-scripticus-svr` (D61) from a public repo and have the
      client fetch it, so standing a registry up starts at `pipx install
      scripticus` — one install path for both halves, and no raw URL to copy.
      The script is already self-contained and takes its compose source from
      `SCRIPTICUS_COMPOSE_URL`, so this is a delivery change, not a rewrite.
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
- [ ] `autoremove`: drop packages that arrived as dependencies and are no longer
      needed by anything installed. `uninstall` only ever removes what the user
      named, and reports orphans instead (D53 for tools, D57 for libraries); a
      single explicit command is the right home for actually removing them, for
      every package kind at once rather than libraries specially.
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
