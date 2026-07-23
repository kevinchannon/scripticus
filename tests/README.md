# End-to-end tests

Black-box tests that drive the real `scripticus` client against a real,
fully-stood-up registry — proxy, index service, and Gitea — and check the
claims the [client README](../client/README.md) makes. They're orchestrated by
[Tasktree](https://github.com/kevinchannon/tasktree):

```console
$ tt e2e-test
```

That resolves the `build` task first (wheels → `dist/`, cached on the packaged
source), then runs [`e2e.sh`](e2e.sh) inside a **containerised runner** — the
[`e2e-tests.dockerfile`](e2e-tests.dockerfile) toolchain image (docker CLI +
compose, BATS, python), with the host Docker socket mounted in
(**docker-out-of-docker**) and the repo auto-mounted at its host path. Inside
the runner, `e2e.sh`:

1. **Installs the client** from the `build` wheels into a venv (the wheels'
   internal same-minor pins target PyPI releases that don't exist for this
   `0.0.0.dev0` tree, so the three workspace wheels go in `--no-deps` and their
   third-party deps follow).
2. **Stands the bundle up + bootstraps a test user** via the shared
   [`scripts/start-server`](../scripts/start-server) (the same script
   `tt start-server` uses for a local dev instance), in e2e mode: build the
   index *from source* ([`server/Dockerfile`](../server/Dockerfile)) as sibling
   containers on the host daemon, **join the stack's network** so it reaches
   services by name (`http://proxy`, `http://gitea:3000`), and create a Gitea
   user + publish token (a namespace *is* a Gitea user, so it must exist before
   any test runs). The compose stack is the shipped
   [`docker-compose.yml`](../docker-compose.yml) **plus** the build overlay
   [`docker-compose.build.yml`](docker-compose.build.yml) (index from source)
   **plus** [`docker-compose.e2e.yml`](docker-compose.e2e.yml) (`!reset` the
   host ports so it's fully internal — no collision with a dev stack on
   `:3000`/`:8000`). The shipped compose stays pull-based; the overlays add the
   source build and the port reset.
3. **Runs the BATS suite**, driving the client over the single front URL (D45).

The stack is torn down (and the runner disconnected) on exit. To leave it up
for debugging:

```console
$ KEEP_UP=1 tt e2e-test
```

CI runs the same `tt e2e-test` ([.github/workflows/e2e.yml](../.github/workflows/e2e.yml));
`ubuntu-latest` provides the Docker socket the DooD runner needs.

## Layout

| File | Role |
| --- | --- |
| `e2e.sh` | Runs inside the runner: install client → (start-server: up + bootstrap) → BATS → down. |
| `e2e-tests.dockerfile` | The runner toolchain image (docker CLI + compose, BATS, python) — no client baked in. |
| `docker-compose.build.yml` | Overlay: build `index` from source (shared with `tt start-server`). |
| `docker-compose.e2e.yml` | Overlay: `!reset` host ports so the e2e stack is fully internal. |
| `lib/helpers.bash` | Per-test setup (isolated `SCRIPTICUS_HOME`), login and publish helpers. |
| `*.bats` | The specs. |

The bundle stand-up + test-user bootstrap lives in
[`scripts/start-server`](../scripts/start-server), shared with `tt start-server`.
The `build`, `unit-test`, and `e2e-test` tasks (and the `e2e` runner) are
defined in the repo-root [tasktree.yaml](../tasktree.yaml).

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
covered in depth by the pytest suite. Each test authors a uniquely-named
package (Gitea persists for the whole run, so identities must not collide
between tests) and gets a fresh, isolated `SCRIPTICUS_HOME`.
