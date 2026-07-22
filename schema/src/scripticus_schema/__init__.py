"""The shared Scripticus wire and manifest schema — the declarative shapes.

Code lives in this package only if it is a *data shape* the client and server
exchange or that defines what a package is: the Pydantic wire models (the read,
resolve, publish, and whoami APIs) and the manifest model (D29). The pure
computations both sides must perform identically — hashing, version ordering,
version-spec parsing, identity globbing — live in ``scripticus_common`` instead
(D51).
"""
