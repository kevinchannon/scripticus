# End-to-end tests

Black-box tests that drive the real `scripticus` client against a real,
fully-stood-up registry — proxy, index service, and Gitea — and check the
claims the [client README](../client/README.md) makes. Everything runs in
Docker, so the only host requirements are **docker** (with the compose
plugin), **curl**, and **python3**.

```console
$ tests/run.sh
```

That one command:

1. **Builds** the registry bundle *from source* — the index from
   [`server/Dockerfile`](../server/Dockerfile), the client harness from
   [`Dockerfile.client`](Dockerfile.client) — so a run also proves those
   Dockerfiles still build. This is why the e2e stack is the shipped
   [`docker-compose.yml`](../docker-compose.yml) **plus the
   [`docker-compose.e2e.yml`](docker-compose.e2e.yml) overlay**: the shipped
   compose stays pull-based (the README distributes it as a source-free,
   one-command standup), and the overlay swaps `index` to a build and adds the
   `client-test` service.
2. **Stands up** proxy + index + Gitea and waits for them to be healthy.
3. **Bootstraps** Gitea ([`bootstrap.sh`](bootstrap.sh)): a brand-new Gitea has
   no users and no token, and a Scripticus namespace *is* a Gitea user, so this
   provisions a user + publish token before any test runs.
4. **Runs the BATS suite** inside the `client-test` container, which has the
   client `pip install`ed from locally-built wheels (the real install path, not
   `uv run`) and reaches the registry at `http://proxy` by service name — the
   single front URL a real client points at (D45).

The stack is torn down on exit. To leave it running for debugging:

```console
$ KEEP_UP=1 tests/run.sh
```

## Layout

| File | Role |
| --- | --- |
| `run.sh` | Orchestrator and single entry point (build → up → bootstrap → test → down). |
| `bootstrap.sh` | Provisions the Gitea user + publish token; prints the token. |
| `docker-compose.e2e.yml` | Overlay: builds `index` from source, adds the `client-test` service. |
| `Dockerfile.client` | The client harness image: `scripticus` (local wheels) + BATS. |
| `lib/helpers.bash` | Per-test setup (isolated `SCRIPTICUS_HOME`), login and publish helpers. |
| `*.bats` | The specs. |

## The specs

- [`lifecycle.bats`](lifecycle.bats) — the full happy path: author → pack →
  login → publish → discover (`list` + `search`) → install → run the installed
  command.
- [`update_yank.bats`](update_yank.bats) — post-install version movement:
  `update` floating a package to a newer version, and `yank`/`--undo` moving a
  version out of and back into read-side resolution.
- [`commands.bats`](commands.bats) — command-shim claims: a multi-command
  package exposing a shim per command (and the guaranteed `<ns>.<pkg>.<cmd>`
  form), `uninstall` removing a package's shims, and `use` re-pointing a
  contested convenience shim.

These check the README's claims aren't lies; the mechanics themselves are
covered in depth by the pytest suite.

Each test authors a uniquely-named package (Gitea persists for the whole run,
so identities must not collide between tests) and gets a fresh, isolated
`SCRIPTICUS_HOME`.
