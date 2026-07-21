# Scripticus server

The index service for [Scripticus](https://github.com/kevinchannon/scripticus),
a package manager and registry for scripts. The server provides
manifest-aware search, version listing, and the atomic publish path for
a Scripticus registry. Installing this package provides the
`scripticus-svr` command.

## Running the server

`scripticus-svr` starts the service, printing its version and address on
start-up:

```console
$ scripticus-svr --host 0.0.0.0 --port 8000
scripticus-svr 0.1.0 — serving on http://0.0.0.0:8000 (interactive API docs at http://0.0.0.0:8000/docs)
```

Both options are optional; the default is `127.0.0.1:8000`. The API is
self-describing: interactive docs are served at `/docs` and the OpenAPI
spec at `/openapi.json`.

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

The index database defaults to a local SQLite file
(`scripticus-index.db`); set `SCRIPTICUS_INDEX_DB` to any SQLAlchemy URL
to point elsewhere. Tables are created automatically on first use.

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

### Docker

Server releases publish a Docker image to
[`kevinchannon/scripticus-server`](https://hub.docker.com/r/kevinchannon/scripticus-server)
(tagged with the release version and `latest`). The repository's
`docker-compose.yml` is the full registry bundle — the index service plus
the Gitea instance that provides storage, authentication, and namespace
ownership — and needs no checkout:

```console
$ curl -LO https://raw.githubusercontent.com/kevinchannon/scripticus/main/docker-compose.yml
$ docker compose up -d
$ curl http://localhost:8000/health
{"status":"ok"}
```

First-run Gitea setup: accounts and organisations are managed in Gitea
(http://localhost:3000), and a Scripticus namespace *is* a Gitea user or
organisation, claimed first-come-first-served, with publish rights
following Gitea's own membership and ACLs. So, once the bundle is up:

1. Register your user in the Gitea web UI (the first registered user is
   the instance admin), and create an organisation for any shared
   namespace you want.
2. Generate a token under *Settings → Applications → Manage Access
   Tokens* with package write and user read scopes.
3. Publish with that token (see above).

## Licence

MIT
