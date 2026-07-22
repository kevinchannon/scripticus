"""Pure, deterministic helpers the Scripticus client and server both compute.

The charter this package lives or dies by (D51): code belongs here only if it
is *pure* (no I/O, no network, no framework, no global state) and must produce
*identical* results on both sides of the wire, so client and server can never
disagree. Today that is content-address hashing (``treehash``), semantic-
version ordering (``semver``), version-spec parsing (``version_spec``), and
package-identity globbing (``identity_glob``).

Not here: Pydantic wire and manifest models (those are ``scripticus_schema``),
anything that does I/O or HTTP, and anything only one side uses. "It's handy to
share" is not the test — "both ends must compute it the same way" is.
"""
