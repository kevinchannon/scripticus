# scripticus-schema

The shared wire and manifest schema between the
[Scripticus](https://pypi.org/project/scripticus/) client and
[server](https://pypi.org/project/scripticus-server/): the package manifest
model and validation, and the Pydantic wire models for the read, resolve,
publish, and whoami APIs.

This package is internal plumbing — install `scripticus` (the CLI) or
`scripticus-server` (the registry) instead; both depend on this package
and pull it in automatically.

Code lives here only if it is a declarative *data shape* — something the
client and server exchange, or that defines what a package is. The pure
computations both sides must perform identically (hashing, version ordering,
version-spec parsing, identity globbing) live in
[`scripticus-common`](https://pypi.org/project/scripticus-common/) instead.
