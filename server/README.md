# Scripticus server

The index service for [Scripticus](https://github.com/kevinchannon/scripticus),
a package manager and registry for scripts. The server provides
manifest-aware search, version and dependency resolution, and the publish
path for a Scripticus registry. Installing this package provides the
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

### Docker

Server releases publish a Docker image to
[`kevinchannon/scripticus-server`](https://hub.docker.com/r/kevinchannon/scripticus-server)
(tagged with the release version and `latest`). The repository ships a
`docker-compose.yml` running it as a single container — no checkout
needed:

```console
$ curl -LO https://raw.githubusercontent.com/kevinchannon/scripticus/main/docker-compose.yml
$ docker compose up -d
$ curl http://localhost:8000/health
{"status":"ok"}
```

The intended v1 deployment pairs the index service with a Gitea instance
that provides storage, authentication, and namespace ownership; Gitea
integration doesn't exist yet, so the compose file is currently
server-only and does not stand up a working registry.

Once Gitea is part of the bundle: accounts and organisations are managed
in Gitea; a Scripticus namespace is a Gitea user or organisation, claimed
first-come-first-served, and publish rights follow Gitea's own membership
and ACLs. The `library` namespace is reserved.

## Licence

MIT
