# scripticus-common

Pure, deterministic helpers shared by the
[Scripticus](https://pypi.org/project/scripticus/) client and
[server](https://pypi.org/project/scripticus-server/): content-addressed
identity (tree hash), semantic-version ordering, version-spec parsing, and
package-identity globbing.

This package is internal plumbing — install `scripticus` (the CLI) or
`scripticus-server` (the registry) instead; both depend on this package and
pull it in automatically.

Code lives here only if it is pure (no I/O, no framework) and must be computed
*identically* by client and server — the test is "both ends must agree on the
result," not "it's handy to reuse." Declarative wire and manifest models live
in [`scripticus-schema`](https://pypi.org/project/scripticus-schema/) instead.
