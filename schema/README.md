# scripticus-schema

The shared contract between the [Scripticus](https://pypi.org/project/scripticus/)
client and [server](https://pypi.org/project/scripticus-server/): the
package manifest schema and validation, the naming and versioning rules,
and the content-addressed identity (tree hash) of a package.

This package is internal plumbing — install `scripticus` (the CLI) or
`scripticus-server` (the registry) instead; both depend on this package
and pull it in automatically.

Code lives here only if it defines what a package *is* or how client and
server communicate. Utilities that merely happen to be useful to both
sides do not belong here.
