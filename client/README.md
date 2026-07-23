# Scripticus

The client for [Scripticus](https://github.com/kevinchannon/scripticus), a
package manager and registry for scripts. Publish, discover, version, and
install the scripts your team shares — with proper namespacing, semver,
dependency resolution, and a single `bin` directory on your PATH — instead of
copying them around from wikis, chat, and assorted git repos.

## Installing

The client requires Python 3.11+. Install it as an isolated CLI tool with
[pipx](https://pipx.pypa.io) (recommended) or [uv](https://docs.astral.sh/uv/) —
either puts `scripticus` on your PATH without touching your project or system
Python:

```console
$ pipx install scripticus       # or: uv tool install scripticus
$ scripticus init               # creates ~/.scripticus, adds bin dir to PATH
```

Restart your shell (or re-source your profile) so `~/.scripticus/bin` is on
your PATH.

Point the client at your organisation's registries. Your org's onboarding
docs will give you the exact lines to run:

```console
$ scripticus config remote add tools https://scripticus.example.com
$ scripticus config tools --install="apt-get install -y {packages}" --escalate=sudo
```

Remotes are searched in the order you add them, which is also the search path
for bare package names — so add them the way your organisation expects them to
resolve. `config remote list` shows the current set.

## Everyday usage

### Finding packages

There are two discovery verbs, for two different questions.

**`search`** — "find me something that does X". It matches package *content*:
name, description, and command names.

```console
$ scripticus search backup --platform linux --lang bash
Package               Latest   Description
infra/backup-rotate   1.2.0    Rotate and prune backup sets
tools/db-backup       0.9.1    Dump and archive databases
```

`search` queries every configured remote in priority order (unlike `install`,
which stops at the first remote that has the package) and merges the results,
each shown at its latest non-yanked version. With more than one remote hit, a
`Remote` column shows which one each result came from; `--remote <name>`
restricts the search to a single remote. If a remote is unreachable it's
reported as a warning and the rest of the results still show. The optional
`--platform` and `--language` (or `--lang`) filters narrow results to packages
that publish a matching artifact.

**`list`** — "show me what's there, by name". It enumerates package *identity*
with a shell glob over `namespace/name`, dnf-style: an *Installed* section from
your machine and an *Available* section from the remotes.

```console
$ scripticus list 'infra/*'
Installed packages
Package               Version
infra/logrotate       0.4.1

Available packages
Package               Version
infra/backup-rotate   1.2.0
```

A glob containing `/` scopes by namespace (`infra/*`); a bare glob matches the
name in any namespace (`*-backup`). `--installed` restricts to what you have
installed and needs no network; `--available` restricts to the remotes'
catalog (excluding what's already installed). `--remote <name>` picks which
remote supplies the available list.

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
- `--skip-tools`: skip the system-tool check and installation entirely.

Required system tools missing from your `PATH` are installed *before* any
package file or shim is written, by running the command your machine's
`[tools]` configuration provides (see [Configuration](#configuration)). If a
required tool is missing and no installer is configured, the install aborts
listing the tools — install them yourself, configure `[tools] install`, or
re-run with `--skip-tools`. Optional tools are only reported, never
installed.

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

Every command is installed under three names: the bare command, a
namespace-qualified form, and a fully-qualified form —

```console
$ backup-verify --help                        # bare (convenient, can collide)
$ infra.backup-verify --help                  # namespace-qualified
$ infra.backup-rotate.backup-verify --help    # <namespace>.<package>.<command>
```

The fully-qualified form is guaranteed unique, so every installed command
is always runnable no matter what else you install. The two shorter forms
are conveniences: if another package provides the same command name, the
most recently installed package takes the contested name (you are warned
at install time, as above). To re-point a contested name at a specific
package, name the shim you want changed:

```console
$ scripticus use tools/old-backup backup-rotate        # the bare shim
$ scripticus use infra/other-tool infra.backup-rotate  # a namespaced shim
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

### Multi-file packages

A package is a directory, so an entrypoint can call sibling helper scripts or
read sibling data files. The whole tree is packed and installed together, and
a command's shim runs its entrypoint **by absolute path** from wherever you
happen to be standing — it does *not* `cd` into the package, and the package
directory is *not* added to `PATH`. So there is one rule for reaching a
sibling:

> **Resolve siblings relative to your own script file, never relative to the
> current directory.** A command runs with the *user's* working directory, not
> the package's, so `./helper.sh` or a bare `open("data.txt")` looks in the
> wrong place. Anchor on the script's own location instead:

| Language | Do this | Not this |
| --- | --- | --- |
| bash | `dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; "$dir/helper.sh"` | `./helper.sh` |
| python | `Path(__file__).resolve().parent / "helper.py"` | `open("helper.py")` |
| powershell | `Join-Path $PSScriptRoot 'helper.ps1'` | `.\helper.ps1` |

This is ordinary script hygiene — the same code would break under any bin-dir
installer — and it is the reason a plain `./helper.sh` cannot be made to work
no matter how the shim is written. Only helper scripts you want to expose as
their *own* commands need a `[commands]` entry; internal helpers and data files
just ride along in the tree.

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
Logged in to origin (https://scripts.example.com) as kevin-c
```

The first time you log in to a remote that isn't already in `config.toml`,
give its URL too — this registers the remote as well as authenticating:

```console
$ scripticus login origin https://scripts.example.com
Token: ********
Logged in to origin (https://scripts.example.com) as kevin-c
```

`login` verifies the token against the remote before storing it and prints
the Gitea account it authenticated as, so a mistyped token fails right away
rather than at your first publish. A rejected token, or a remote that can't
be reached, is reported as such and nothing is written.

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
existing consumers do not break. `yank` takes an *exact* version (it is
whole-version — a range is rejected), and needs a token for the namespace,
exactly like `publish`.

Changed your mind? `--undo` reverses a yank — the same version becomes visible
again, with no time limit on when you can do it:

```console
$ scripticus yank --undo infra/backup-rotate@1.2.0
```

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

  An optional `[tools]` table tells Scripticus how to install missing
  *required* system tools. Scripticus encodes no package-manager logic — you
  provide the command, and the missing tool names are substituted into a
  `{packages}` placeholder (shell-quoted; appended if the placeholder is
  absent). It runs once through your shell, inheriting the environment, so
  proxies/mirrors/credentials come from the machine environment rather than
  this (org-distributable) file:

  ```toml
  [tools]
  install = "apt-get install -y {packages}"   # your machine's package manager
  escalate = "sudo"                            # optional; elevates only this command
  ```

  `escalate` is prepended to the tool command alone — Scripticus itself never
  needs privilege (its state is entirely under `~/.scripticus`). Leave it out
  when already root or on Windows-as-admin. With no `[tools] install`
  configured, Scripticus never invokes a package manager: missing required
  tools abort the install (with the `--skip-tools` escape). Tool
  *satisfiability* in v1 is PATH presence only.

- `credentials.toml` — one Gitea access token per remote, keyed by URL and
  registered with `scripticus login`. Kept separate from `config.toml` so
  org-distributed configuration never carries a token.
- `installed.lock` — install state: every installed package, its exact
  resolved version and content hash, the full resolved dependency closure
  (with direct vs transitive marking), and provenance (remote or local file).
- `bin/` — the shim directory on your PATH.

## Licence

MIT
