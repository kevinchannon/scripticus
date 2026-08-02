# Changelog

Each workspace package (`common`, `schema`, `server`, `client`) is released and
versioned on its own tag, so this file is grouped by **package**, not by a
single project-wide version.

**Release notes are for users of the published package.** A bullet belongs here
only if it changes what someone installing `scripticus` or `scripticus-server`
can see or do — behaviour, CLI surface, wire format, or the README that becomes
the PyPI page. Test infrastructure, CI, and repo tooling do not go in, however
substantial: this file becomes the release tag's message and the GitHub release
body, so anything else is noise on a page people read to decide whether to
upgrade. The git history is where that work is recorded.

**To record a change**, add a bullet to the `### <package>` block under
`## Unreleased`. **`tt release` does the rest**: when it releases a package it
takes that block, uses it as the annotation message of the release tag
(`<package>-vX.Y.Z`), and moves it into a released section below — in the same
commit as the dependency-pin bump, so the tag points at a commit whose
changelog already records the release (see D59, and the Releasing runbook in
[CLAUDE.md](CLAUDE.md)).

Releases before this file exists are recorded only in the git tags and in
[doc/DECISIONS.md](doc/DECISIONS.md).

## Unreleased

### common

### schema

### server

### client

## server 0.8.1 — 2026-08-02

- Documented how to add publishers to a registry. This release is that
  documentation reaching PyPI, where it is visible — there is no code change.
  An organisation team holding only `Packages: Write` — no repositories, not an
  owner — is enough to publish, and each publisher needs a token scoped
  `organization: Read`, `package: Read and Write`, `user: Read`. A missing
  `read:organization` scope surfaces as `'<user>' cannot publish to namespace
  '<ns>'`, which reads as a team-permissions problem and is not one; the README
  gives the one command that tells the two apart.

## client 0.9.0 — 2026-08-02

- Documented the token scopes publishing actually needs. The previous
  instruction said only "package-write scope", which authenticates fine and
  then fails at publish: `read:organization` and `read:user` are needed too.
  Also notes what an install-only token needs, and that scopes cannot be
  edited after a token is created.
- `publish` accepts one of the version's archives, not only the
  `<name>-<version>` prefix they share (D65) — so tab-completing the filename
  now works instead of failing with a message that blamed a missing build.
  Either form selects the same batch; naming one archive of a multi-variant
  version says so before uploading. A path that looks like an archive filename
  but is not one gets an error that says that, rather than suggesting `pack`.

## server 0.8.0 — 2026-08-01

- The bundle now publishes a single port (D62). Gitea's web UI moved behind the
  proxy at `/accounts/`, so there is no second port to open, firewall, or
  certificate — set `SCRIPTICUS_PUBLIC_URL` (or `get-scripticus-svr
  --public-url`) when the registry is not reached at `http://localhost:8000`,
  since Gitea builds its links from it. The instance is also retitled
  Scripticus, which the bootstrap script applies.
- `get-scripticus-svr` asks for the administrator's account and the publishing
  namespace separately (D61), creating the latter as a Gitea organisation owned
  by the admin. Defaulting the namespace to whoever ran the installer would put
  an individual's name in every package reference — `phil-from-it/backup-rotate`
  instead of `acme-co/backup-rotate` — which nothing can undo once published.
  The minted token now carries `read:organization` so publishing to an
  organisation works without a second trip to the web UI.
- Added `get-scripticus-svr`, a bootstrap script that stands a registry up in one
  command — `curl -fsSL <url> | sh` (D61): it preflights the host, fetches the compose bundle, starts it,
  and creates a Gitea admin account with a correctly-scoped publish token,
  printing the password and token once. It refuses rather than repairs — an
  existing directory, stack, or Gitea user stops the run.
- The bundle's host port is now `${SCRIPTICUS_PORT}`, defaulting to the
  documented 8000, so a second stack can coexist with an existing one.
- Documented the Gitea token scopes properly: `organization: Read` is also needed
  to publish under an organisation namespace, which the previous instructions
  omitted.
- Fixed the registry bundle's documented standup: `docker-compose.yml` bind-mounted
  `./proxy/Caddyfile`, which does not exist for an operator who fetches only the
  compose file as the README instructs — Docker created an empty directory at the
  mount path and the proxy container failed to start. The Caddyfile is now an
  inline `configs` entry in the compose file, so the one-file standup works (D45).
  Needs Docker Compose v2.23.1 or newer.

## client 0.8.0 — 2026-08-01

- Scripticus now suggests a system-tool installer instead of making you write
  one (D64). It ships a table of the common package managers — apt/dnf/yum/
  zypper/pacman/apk/xbps, pkg, Homebrew/MacPorts, winget/Chocolatey/Scoop —
  detects which one is on your PATH, and the first time an install needs a
  missing tool it shows the exact command and asks. Accepting saves it to
  `config.toml`, so it asks once per machine. Nothing is asked at setup time,
  a non-interactive run (`-y`/`--force`) never prompts and never saves, and a
  suggestion only becomes a command once you have accepted it. `scripticus
  config tools` shows the suggestion for your machine.
- `scripticus init` is no longer a step you have to remember: `install` runs the
  same bootstrap itself (D63), creating `~/.scripticus`, putting its `bin`
  directory on your PATH, and saying so. Setting a machine up is now `pipx
  install scripticus` followed by one `scripticus login <name> <url>`, which
  registers the remote as well as storing the token. `init` still exists for
  doing the PATH change up front.

## common 0.2.0 — 2026-07-28

- Added `language_compat`: the pure rule deciding which libraries a package can
  source — `sh` serves the whole shell family, `bash` only `bash` — computed
  identically by the server's resolver and the client's install check (D57).

## schema 0.5.0 — 2026-07-28

- Added the `[library]` package kind (D57): a fieldless marker, mutually
  exclusive with `[commands]` and `[snippet]`, with the sourced entry point at
  `src/load.<ext>` and the language restricted to `sh` or `bash`.
- Added `sh` as a language in its own right — the POSIX baseline, which also
  makes portable `sh` *commands* possible.
- `app` is no longer a valid command name (D60). It would produce shims named
  `*.app`, which recent macOS refuses to execute — taking them for application
  bundles and killing them without output. Rejected at pack and publish, with
  an error that says why. Package names are unaffected.

## server 0.7.0 — 2026-07-28

- Publish projects a library version into a new `library` index table, and
  `/search` and `/packages` report `kind: "library"` (D57).
- `/resolve` rejects a closure in which something sources a library it cannot
  source, naming both packages and their languages.

## client 0.7.0 — 2026-07-28

- Libraries (D57): `scr_load <namespace>/<name>` sources reusable shell code
  into a script — transitive, idempotent, and non-fatal on a miss. Installed
  libraries stage to a version-less `~/.scripticus/lib/<namespace>/<name>/`, so
  updating one is picked up without rebuilding its consumers.
- `sh`/`bash` command shims now source the loader before the script instead of
  `exec`ing it, so `scr_load` is in scope with no arrangement by the author.
  Other languages are unchanged.
- `scripticus init` exports `SCRIPTICUS_LIB` alongside PATH, so ad-hoc scripts
  can opt in with `. "$SCRIPTICUS_LIB/scr_load.sh"`.
- `scripticus new <sh|bash> <name> --lib` scaffolds a library package.
- `uninstall` reports libraries nothing left depends on rather than removing
  them, and `list`/`search` tag a library row as such.

