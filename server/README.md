# Scripticus server

The index service for [Scripticus](https://github.com/kevinchannon/scripticus),
a package manager and registry for scripts. The server provides
manifest-aware search, version listing, and the atomic publish path for
a Scripticus registry. Installing this package provides the
`scripticus-svr` command.

## Running the server

The recommended way to run a Scripticus registry is the Docker Compose
bundle: a reverse proxy fronting the index service and the Gitea instance
that provides storage, authentication, and namespace ownership. Server
releases publish a Docker image to
[`kevinchannon/scripticus-server`](https://hub.docker.com/r/kevinchannon/scripticus-server)
(tagged with the release version and `latest`), and the repository's
`docker-compose.yml` wires the whole bundle together — no checkout needed.
The proxy is the single URL clients use (`http://localhost:8000`): it routes
blob downloads to Gitea and everything else to the index, so a client needs
no Gitea address of its own (D45). The bundle publishes exactly one port:
account management is served through the same front at
`http://localhost:8000/accounts` (D62), so there is no second port to open.

The `get-scripticus-svr` script does the whole standup (D61) — it checks the
host, fetches the compose bundle, starts it, and creates your Gitea admin
account with a correctly-scoped publish token:

```console
$ curl -fsSL https://raw.githubusercontent.com/kevinchannon/scripticus/main/get-scripticus-svr | sh
```

It asks three things — where to put the stack, the administrator's account
name, and the organisation to publish under — then prints a username,
password, and token. **Save all three then** — Gitea shows a token once, and
the script will not mint a second one for an account that already exists.

The administrator and the publishing namespace are deliberately separate. A
namespace is a Gitea user *or organisation*, so the person running the registry
does not have to be the identity packages belong to: answer `acme-co` to the
namespace question and packages read `acme-co/backup-rotate`, while the admin
account stays a person. Skipping it publishes under the admin's own username,
which is fine for a personal registry and a trap for a shared one — every
reference already published keeps that name.

Options go after `-s --`, since a pipe leaves nowhere else to put them:

```console
$ curl -fsSL .../get-scripticus-svr | sh -s -- --dir /srv/registry --port 9000
```

`--dir`, `--user`, `--org`, `--email`, and `--port` cover the non-interactive case;
`--help` lists them. Pass `--public-url` if the registry will not be reached at
`http://localhost:<port>` — the account pages build their links from it, so
getting it wrong leaves a working registry with broken links.

If you would rather read it before running it — a fair instinct for anything
piped into a shell — download it first and run it separately; the script
behaves identically either way:

```console
$ curl -fsSLO https://raw.githubusercontent.com/kevinchannon/scripticus/main/get-scripticus-svr
$ less get-scripticus-svr
$ sh get-scripticus-svr
```

It refuses rather than repairs: an existing directory, compose stack, or Gitea
user stops the run rather than modifying what is already there. It needs
Docker, the Compose v2 plugin at v2.23.1 or newer (the bundle carries its proxy
config inline), and either `curl` or `wget`.

The script sets up the administrator only. To let anyone else publish, see
[Adding publishers](#adding-publishers).

### Running the index service directly

Of course, you can also just run the index service on its own — without
the proxy-and-Gitea bundle — which is handy for development or slotting it
into infrastructure you already operate. `scripticus-svr` starts the
service, printing its version and address on start-up:

```console
$ scripticus-svr --host 0.0.0.0 --port 8000
scripticus-svr 0.1.0 — serving on http://0.0.0.0:8000 (interactive API docs at http://0.0.0.0:8000/docs)
```

Both options are optional; the default is `127.0.0.1:8000`. The API is
self-describing: interactive docs are served at `/docs` and the OpenAPI
spec at `/openapi.json`.

Run this way, the index service still needs a Gitea instance to publish
against (`SCRIPTICUS_GITEA_URL`, default `http://localhost:3000`) and an
index database — a local SQLite file (`scripticus-index.db`) by default,
or set `SCRIPTICUS_INDEX_DB` to any SQLAlchemy URL to point elsewhere;
tables are created automatically on first use. The Compose bundle provides
both for you.

### Health check

`GET /health` returns `200` with `{"status": "ok"}` while the service is
up. It is deliberately unauthenticated — it's a liveness probe for load
balancers and container orchestrators.

### Version

`GET /version` returns the running server's version, e.g.
`{"version": "0.1.1"}`.

### Package index (read API)

- `GET /packages/{namespace}/{name}` — a package's version listing, newest
  first by semver precedence. Yanked versions are included and marked
  (`"yanked": true`) so pinned lookups can still see them; unknown packages
  return `404`.
- `GET /search?q=<substring>&platform=<os>&language=<lang>` — packages whose
  name contains `q` (all parameters optional), with each result's latest
  non-yanked version. Yanked versions are invisible to search; `platform`
  and `language` filter on the artifacts a version actually provides.

### Publishing

`POST /packages` publishes a package version: a multipart upload of one
or more archives — a version's whole format-group set, as produced by
`scripticus pack`, one repeated `archives` part each — with your Gitea
token in the `Authorization` header. This is what `scripticus publish`
does for you; the raw request looks like:

```console
$ curl -X POST http://localhost:8000/packages \
    -H "Authorization: token <your-gitea-token>" \
    -F archives=@my_tool-1.0.0-linux.macos-bash.tar.gz \
    -F archives=@my_tool-1.0.0-windows-bash.zip
```

The server trusts nothing about the upload: it re-validates every
archive's manifest and package tree, checks the batch is one content
tree in different archive formats, computes the content hash, checks
with Gitea (live) that your token may publish to the manifest's
namespace — your own username, or an organisation you belong to —
stores the blobs in Gitea's generic package registry, and only then
commits the index record. The batch is atomic: if any archive fails
validation or any write fails, nothing is published.
Versions are immutable; the one addition an existing version accepts is
an artifact in a new archive format carrying the identical content hash.
Declared package dependencies must be fully namespaced and already
present in the index, and a publish that would create a dependency cycle
is rejected. The `library` namespace is reserved. The Gitea instance is
configured with `SCRIPTICUS_GITEA_URL` (default `http://localhost:3000`).

### Yanking

`PATCH /packages/{namespace}/{name}/{version}` with a JSON body of
`{"yanked": true}` hides a published version from search and `latest`/range
resolution while leaving it fetchable by anything that pins it exactly — no
hard delete. `{"yanked": false}` reverses it. Auth is the same live,
namespace-scoped Gitea check as publishing, so only a namespace owner can
yank. This is what `scripticus yank` (and `yank --undo`) does for you:

```console
$ curl -X PATCH http://localhost:8000/packages/infra/backup-rotate/1.2.0 \
    -H "Authorization: token <your-gitea-token>" \
    -H "Content-Type: application/json" \
    -d '{"yanked": true}'
```

Unlike publish, this touches no Gitea blob — it flips one flag on the index
record. An unknown version is a 404; yank is idempotent and carries no time
window, so a version can be un-yanked at any time.

## Adding publishers

`get-scripticus-svr` sets up one account: the administrator, who owns the
publishing organisation. Everyone else you want publishing needs two things,
and they are independent — getting one without the other produces an error
that names the wrong cause.

Accounts, organisations, and teams are Gitea's (D2/D4), so all of this happens
in the account pages the bundle serves at `http://localhost:8000/accounts/`.

### 1. Membership of the publishing organisation

The index service asks Gitea, live, whether the publishing user *is* the
namespace or belongs to it. Membership of any team in the organisation
satisfies that check.

You do not have to make people organisation owners to let them publish. A team
with only the packages permission is enough — and is what you want, since
Owners can also delete the organisation:

**Organisation → Teams → New Team**

| Setting | Value |
| --- | --- |
| Permission | *not* Administrator access |
| `Packages` unit | **Write** |
| Every other unit | None |
| Repositories | none — leave the team with no repositories at all |

The packages permission is organisation-level, not per-repository, so a team
with zero repositories publishes perfectly well. Add your publishers to that
team.

A read-only counterpart (`Packages`: **Read**) is worth having too, for people
who only install. Search, listing, and resolution are anonymous — only the blob
download is authenticated — so an installer's token needs just `user: Read`
(the client verifies it at login) and `package: Read`.

### 2. A correctly-scoped token, per publisher

Each publisher generates their own token under **Settings → Applications**,
with `organization: Read`, `package: Read and Write`, and `user: Read`. The
[client README](https://github.com/kevinchannon/scripticus/blob/main/client/README.md#publishing)
covers this from the publisher's side; the part worth knowing as an
administrator is that **a missing `read:organization` scope looks exactly like
a permissions problem**. The publish fails with:

```text
error: publish to 'origin' failed (403): 'writer' cannot publish to namespace 'acme-co'
```

which sends you to check org membership and team permissions that are, in
fact, already correct. If a publisher hits that and you have satisfied step 1,
check the token's scopes before touching anything in the organisation. Gitea
will tell you plainly:

```console
$ curl -H "Authorization: token <their-token>" \
    http://localhost:8000/accounts/api/v1/orgs/<org>/members/<user>
{"message":"token does not have at least one of required scope(s), required=[read:organization], token scope=write:package,read:user"}
```

`204 No Content` there means the token can see the membership and step 1 is
done. Scopes cannot be edited after a token is created, so the fix is always a
new token plus `scripticus login`.

## Licence

MIT
