# Scripticus server

The index service for [Scripticus](https://github.com/kevinchannon/scripticus),
a package manager and registry for scripts. The server provides
manifest-aware search, version and dependency resolution, and the publish
path for a Scripticus registry. Installing this package provides the
`scripticus-svr` command.

## Standing up a server

The server is a Docker Compose bundle containing the Scripticus index service
and a Gitea instance that provides storage, authentication, and namespace
ownership:

```console
$ curl -LO https://example.com/scripticus/docker-compose.yml
$ docker compose up -d
```

Accounts and organisations are managed in Gitea; a Scripticus namespace is a
Gitea user or organisation, claimed first-come-first-served, and publish
rights follow Gitea's own membership and ACLs. The `library` namespace is
reserved.

## Licence

MIT
