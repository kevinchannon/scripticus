# Scripticus

The client for [Scripticus](https://github.com/kevinchannon/scripticus), a
package manager and registry for scripts. Publish, discover, version, and
install the scripts your team shares — with proper namespacing, semver,
dependency resolution, and a single `bin` directory on your PATH — instead of
copying them around from wikis, chat, and assorted git repos.

## Installing

The client requires Python 3.11+.

```console
$ pip install scripticus
$ scripticus init            # creates ~/.scripticus, adds bin dir to PATH
```

Restart your shell (or re-source your profile) so `~/.scripticus/bin` is on
your PATH.

If your organisation distributes a standard configuration, pull it in one
step:

```console
$ scripticus config install https://git.example.com/org/scripticus-config.git
```

This installs the org's remotes and default namespace search path, so bare
package names resolve the way your organisation expects.

## Everyday usage

### Searching

```console
$ scripticus search backup --platform linux --lang bash
NAMESPACE/NAME          VERSION  LANGUAGE  PLATFORMS      DESCRIPTION
infra/backup-rotate     1.2.0    bash      linux, macos   Rotate and prune backup sets
tools/db-backup         0.9.1    bash      linux          Dump and archive databases
```

### Installing

```console
$ scripticus install infra/backup-rotate
```

With a version or semver range:

```console
$ scripticus install infra/backup-rotate@1.2.0
$ scripticus install "infra/backup-rotate@^1.2"
```

If your namespace search path is configured, bare names work too:

```console
$ scripticus install backup-rotate
```

Bare names are resolved against your configured namespace list in priority
order — they are a client-side convenience; the installed package is always
recorded under its full namespaced identity.

Before anything is written, Scripticus resolves the full dependency set and
shows you a transaction summary:

```text
Installing infra/backup-rotate 1.2.0

New packages:
  infra/backup-rotate   1.2.0   (commands: backup-rotate)
  infra/log-common      2.0.3   (dependency)

Required system tools: jq, curl        [found]
Optional system tools: fzf             [not found — some features degraded]

Shim conflicts:
  backup-rotate  currently owned by tools/old-backup 0.4.0 — will be overwritten

Proceed? [y/N]
```

Non-interactive use:

- `-y` / `--yes` (equivalent to `--force=no-conflicts`): accept the
  transaction, but **abort entirely** (nothing installed, non-zero exit) if it
  would overwrite an existing command shim.
- `--force=all`: accept everything, including shim overwrites. Every
  overwritten shim is reported in the output.

Install from a local archive (no registry involved):

```console
$ scripticus install -f ./some-local-pkg-0.0.1.tar.gz
```

Locally-installed packages are tracked with local provenance; `update` will
skip them with a warning rather than trying to resolve them against a remote.

### Updating and uninstalling

```console
$ scripticus update                 # everything
$ scripticus update backup-rotate   # one package
$ scripticus uninstall backup-rotate
```

`uninstall` shows what will be removed and asks for confirmation (`-y`
skips the prompt). If a removed command is also provided by another
installed package — for example the uninstalled package had taken the shim
over — you are offered a numbered list of replacements to re-point the
command at, with "No replacement" as the default:

```console
$ scripticus uninstall new-backup

Uninstalling tools/new-backup 2.0.0

Command shims to remove: backup-rotate

Proceed? [y/N]: y

Uninstalled tools/new-backup 2.0.0

'backup-rotate' is also provided by other installed packages:
  0) No replacement
  1) tools/old-backup  1.4.2
Select a replacement for 'backup-rotate' [0]: 1
'backup-rotate' now points at tools/old-backup 1.4.2
```

With `-y` no replacement is ever selected automatically; the alternatives
are listed with the `scripticus use` command that would restore each one.

### Command conflicts

If two installed packages expose the same command name, the most recently
installed one owns the shim (you are warned at install time, as above). To
re-point a command at a specific package:

```console
$ scripticus use tools/old-backup backup-rotate
```

The fully-disambiguated form is always available regardless of who owns the
shim:

```console
$ scripticus run infra/backup-rotate -- --dry-run
```

## Authoring packages

### Scaffolding

```console
$ scripticus new bash my-cool-script -n infra
```

The namespace (`-n/--namespace`) is required: it is the namespace the
package will be published under (a Gitea user or organisation), and it goes
straight into the generated manifest. Namespaces are lower-case letters,
digits, and dashes, and must begin with a letter.

This creates:

```text
my-cool-script/
├── meta.toml
├── LICENSE
├── README.md
├── src/
│   └── main.sh
└── test/
```

Package names are lower-case with dashes (`my-cool-script`). Script files
inside the package follow the conventions of their own language — a PowerShell
package's named command scripts will be `PascalCase.ps1`, for example.

Because packages are plain scripts, the development loop is direct: `cd` into
the directory and run them. To exercise the *installed* experience (shims,
PATH) while developing:

```console
$ scripticus install --editable .
```

which points the shim at your working directory.

### The manifest

```toml
[package]
namespace = "infra"
name = "backup-rotate"
version = "1.2.0"
language = "bash"
description = "Rotate and prune backup sets"

[platforms]
os = ["linux", "macos"]
distros = ["debian", "arch"]      # optional, narrows os

[dependencies.tools]
requires = ["jq", "curl"]
optional = ["fzf"]

[dependencies.packages]
"infra/log-common" = "^2.0"

# Optional. If omitted, src/main.<ext> is the single entrypoint and the
# command name is the package name.
[commands]
backup-rotate = "src/main.sh"
backup-verify = "src/BackupVerify.sh"
```

Entrypoint rules:

- **No `[commands]` table**: `src/` must contain `main.<ext>` (extension per
  the package language). Typing the package name runs it.
- **`[commands]` table present**: each entry maps a command name to a script
  path. Every listed command gets a shim on install.

Versions must be strict [semver](https://semver.org); publishes with
non-conforming versions are rejected.

> **Manifest accuracy is your responsibility.** Scripticus performs no
> correctness checks on the declared platforms or tool dependencies — neither
> at publish nor install. If the manifest is wrong, the package will be wrong,
> exactly as with a broken `pyproject.toml` or `package.json`. Test your
> packages.

### Packing

To archive a package directory into a distributable artifact:

```console
$ scripticus pack path/to/my-cool-script-proj
$ scripticus pack path/to/my-cool-script-proj -o builds   # write into builds/
```

The manifest is validated first; the archives land in the current directory
unless `-o/--output` names another one (created if needed). One archive is
produced per format the declared target platforms call for — `.tar.gz`
covering the POSIX/macOS targets, `.zip` covering Windows — so a package
targeting both produces two archives with identical content. Filenames carry
wheel-style tags (name, version, platforms, language, with dashes in
name/version normalised to underscores):

```text
my_tool-1.2.0-linux.macos-python.tar.gz
my_tool-1.2.0-windows-python.zip
```

The filename is human-legible redundancy only; the manifest inside the
archive is the source of truth.

### Publishing

Publishing authenticates with a Gitea personal access token: create one in
your Gitea user settings (it needs package-write scope), then log in to a
remote by name:

```console
$ scripticus login origin
Token: ********
```

The first time you log in to a remote that isn't already in `config.toml`,
give its URL too — this registers the remote as well as authenticating:

```console
$ scripticus login origin https://scripts.example.com
Token: ********
```

The token is stored in `~/.scripticus/credentials.toml`, readable only by
you, and sent with each publish — the registry itself holds no credentials.
In CI, set the `SCRIPTICUS_TOKEN` environment variable instead; it takes
precedence over the stored token.

`publish` doesn't pack for you — build the archive(s) first, then point
`publish` at them by `name-version`, the same identifier `pack` just used
for the filenames:

```console
$ scripticus pack my-cool-script
$ scripticus publish my-cool-script-0.1.2
```

The argument is a path whose last segment is `<name>-<version>`; everything
in that directory whose filename matches those fields (D26's tags — dashes
in the name are matched against the filename's underscore form
automatically) gets published. That means a package targeting both format
groups publishes both archives from one command:

```console
$ scripticus pack my-cool-script -o builds
$ scripticus publish builds/my-cool-script-0.1.2
Published my-cool-script 0.1.2:
  my_cool_script-0.1.2-linux.macos-bash.tar.gz
  my_cool_script-0.1.2-windows-powershell.zip
```

Every matched archive goes up in a single request, and the whole batch is
published together or rejected together — the index service validates all
of them before writing any blob to Gitea or committing anything, so there
is no state where one variant is live and another silently isn't. If
publish fails, nothing in that batch was published; fix the problem and
re-run.

With more than one remote configured, `publish` targets the first one listed
in `config.toml` unless you say otherwise:

```console
$ scripticus publish builds/my-cool-script-0.1.2 --remote public
```

A published version is immutable. If you publish something broken:

```console
$ scripticus yank infra/backup-rotate@1.2.0
```

Yanked versions disappear from search and `latest` resolution, but remain
fetchable by anything that pins them directly (including lockfiles), so
existing consumers do not break.

### Platform variants

The same package version may be published as multiple platform/language
variants (for example a `linux`/`bash` artifact and a `windows`/`powershell`
artifact). The client automatically selects the variant matching the
installing machine. POSIX/macOS artifacts are `.tar.gz`; Windows artifacts are
`.zip`.

## Configuration

Client configuration lives in `~/.scripticus/`:

- `config.toml` — remotes as a `[[remotes]]` array of `{ name, url }`
  entries; list order is priority (this list doubles as the bare-name
  namespace search path, and `publish` defaults to the first entry) — and
  other defaults. For example:

  ```toml
  [[remotes]]
  name = "origin"
  url = "https://scripts.example.com"

  [[remotes]]
  name = "public"
  url = "https://scripticus.example.org"
  ```

- `credentials.toml` — one Gitea access token per remote, keyed by URL and
  registered with `scripticus login`. Kept separate from `config.toml` so
  org-distributed configuration never carries a token.
- `installed.lock` — install state: every installed package, its exact
  resolved version and content hash, the full resolved dependency closure
  (with direct vs transitive marking), and provenance (remote or local file).
- `bin/` — the shim directory on your PATH.

## Licence

MIT
