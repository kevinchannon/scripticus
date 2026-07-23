# Scripticus

A package manager and registry for scripts. Publish, discover, version, and
install the scripts your team shares — with proper namespacing, semver,
dependency resolution, and a single `bin` directory on your PATH — instead of
copying them around from wikis, chat, and assorted git repos.

- **Namespaced**: every package lives under an owner (`team/backup-rotate`),
  GitHub-style. No squatting, no collisions.
- **Cross-platform**: one package version can ship separate
  Linux/macOS/Windows variants; the client installs the right one.
- **Multi-file, multi-command**: a package is a directory and can expose
  several commands.
- **Content-addressed**: every artifact is identified by the hash of its
  contents and verified on install.
- **Self-hostable in one command**: the server ships as a Docker Compose
  bundle.

## The pieces

Scripticus is two PyPI packages, developed together in this repository:

- **[`scripticus`](client/README.md)** — the CLI client. Installing,
  authoring, and publishing packages all happen through it. See the
  [client README](client/README.md) for installation and usage.
- **[`scripticus-server`](server/README.md)** — the index service
  (`scripticus-svr`), deployed alongside Gitea. See the
  [server README](server/README.md) for standing up a registry.

The design documents live in [doc/](doc/): [vision](doc/VISION.md),
[architecture](doc/ARCHITECTURE.md), [roadmap](doc/ROADMAP.md), and the
[decision record](doc/DECISIONS.md).

## Developing

The repository is a [uv workspace](https://docs.astral.sh/uv/concepts/projects/workspaces/)
with four members: `client/` and `server/` (the CLI and index service), and
two shared packages they both build on — `schema/` (the declarative wire and
manifest models) and `common/` (pure helpers both sides compute identically:
hashing, versioning, identity globbing). All commands run from the repository
root:

```console
$ uv sync                             # create/update the workspace environment
$ uv run pytest                       # run all tests (all members)
$ uv run scripticus -v                # run the client CLI
$ uv run scripticus-svr               # start the index service (Ctrl-C to stop)
$ uv build --package scripticus      # build the client wheel/sdist into dist/
$ uv build --package scripticus-server
```

All members share a single lockfile (`uv.lock`) and virtual environment.

The full-stack end-to-end tests are separate — orchestrated by
[Tasktree](https://github.com/kevinchannon/tasktree), they stand the whole
registry bundle up from source in Docker and drive the real client against it
(see [tests/README.md](tests/README.md)):

```console
$ tt e2e-test                         # build wheels, stand up, test, tear down
```

## Licence

MIT
