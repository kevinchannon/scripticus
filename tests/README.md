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
   `:8000`). The shipped compose stays pull-based; the overlays add the
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
| `lib/helpers.bash` | Per-test setup (isolated `SCRIPTICUS_HOME`), login, remote-registration, and publish helpers. |
| `*.bats` | The e2e specs — user workflows against the live stack (`tt e2e-test`). |
| `scripts/*.bats` | Shell-script specs — no stack, no client (`tt script-test`). |

The bundle stand-up + test-user bootstrap lives in
[`scripts/start-server`](../scripts/start-server), shared with `tt start-server`.
The `build`, `unit-test`, and `e2e-test` tasks (and the `e2e` runner) are
defined in the repo-root [tasktree.yaml](../tasktree.yaml).

## The e2e specs (`tt e2e-test`)

User workflows against the live stack. Edge and error cases belong in the
pytest suite, which can reach them faster and without a registry; what earns a
place here is a path a user actually walks, or a behaviour that only real Gitea
can demonstrate.

- [`lifecycle.bats`](lifecycle.bats) — the full happy path: author → pack →
  login → publish → discover (`list` + `search`) → install → run the installed
  command.
- [`update_yank.bats`](update_yank.bats) — post-install version movement:
  `update` floating a package to a newer version, and `yank`/`--undo` moving a
  version out of and back into read-side resolution.
- [`snippets.bats`](snippets.bats) — the snippet lifecycle (D58): author
  multi-language boilerplate → publish → find it by snippet name and language →
  install (no shims) → `snip` on stdout via both the standalone binary and
  `scripticus snip`, plus the listing an ambiguous token gets.
- [`commands.bats`](commands.bats) — command-shim claims: a multi-command
  package exposing a shim per command (and the guaranteed `<ns>.<pkg>.<cmd>`
  form), `uninstall` removing a package's shims, and `use` re-pointing a
  contested convenience shim.
- [`org_publish.bats`](org_publish.bats) — publishing to an **organisation**
  namespace (D4), which the specs above never touch: they publish under the test
  user, where `can_publish` short-circuits on `namespace == user` and never asks
  Gitea anything. Covers the two independent requirements — a team with only
  `Packages: Write` (no owner rights, no repositories) being enough to publish,
  and the token needing `read:organization` for the membership check to be
  *askable* at all. The negative test pairs with a control: the same
  under-scoped token still publishes fine to its own user namespace, so the
  failure is provably about the organisation lookup rather than a bad token.

## The shell-script suite (`tt script-test`)

Separate from the e2e suite, and separately runnable: these need no registry, no
client, and no Docker socket, so they run under their own `scripts` runner with
nothing mounted. Still containerised — BATS is something the runner image
provides, not something every contributor has to install.

- [`scripts/bootstrap.bats`](scripts/bootstrap.bats) — it exercises
  [`get-scripticus-svr`](../get-scripticus-svr) (D61). It **stubs `docker` and
  `curl`** rather than driving real ones, because what it tests is the script's decisions — host preflight,
  the three refusals, project-name derivation — and those branches are the ones
  a real stack makes slow or impossible to reach (an old compose plugin cannot
  be conjured on demand). The happy path is deliberately not covered here: the
  script polls `http://localhost:<port>/accounts/`, which assumes it runs on the
  Docker host, and inside the DooD runner localhost is the runner rather than
  the host publishing those ports.

These check the README's claims aren't lies; the mechanics themselves are
covered in depth by the pytest suite. Each test authors a uniquely-named
package (Gitea persists for the whole run, so identities must not collide
between tests) and gets a fresh, isolated `SCRIPTICUS_HOME`.

## Isolation, and why the container isn't enough

The whole suite runs inside one container, against one registry stack — so the
container isolates the *run*, not the tests within it. Two things therefore
still have to be set per test:

- **`SCRIPTICUS_HOME`** (`common_setup`) — one container means one filesystem,
  and 40-odd tests sharing `~/.scripticus` would share remotes, credentials,
  the lockfile, and installed shims. The uninstall spec would reach into the
  `use` spec. A fresh home per test is what makes each one start from nothing;
  the cost is that a test needing a remote must register one, which is what
  `register_remote` is for.
- **`SCRIPTICUS_TOKEN`** — not a path override but an *identity* selector. The
  stack holds several Gitea identities (the test user, the organisation
  publisher, and that publisher under-scoped), and a test that published by
  logging in would overwrite the credential store it shares with the rest of
  the test. Setting the token inline picks who is publishing without touching
  stored state — and is the documented CI path (D34), so the specs exercise a
  real feature rather than a test-only hook.

What the container *does* remove is machine setup: the client is installed from
the freshly built wheels into a throwaway venv, and the runner joins the stack
network, so services are reached by name (`http://proxy`) with no host ports,
no port-mapping, and nothing to collide with a stack you already have running.
The environment the specs read (`SCRIPTICUS_E2E_URL`, `SCRIPTICUS_E2E_TOKEN`,
`SCRIPTICUS_E2E_NAMESPACE`, and the `SCRIPTICUS_E2E_ORG*` set) is exported once
by `e2e.sh` from what `scripts/start-server` bootstrapped.
