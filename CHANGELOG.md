# Changelog

Each workspace package (`common`, `schema`, `server`, `client`) is released and
versioned on its own tag, so this file is grouped by **package**, not by a
single project-wide version.

**To record a change**, add a bullet to the `### <package>` block under
`## Unreleased`. **`tt release` does the rest**: when it releases a package it
takes that block, uses it as the annotation message of the release tag
(`<package>-vX.Y.Z`), and moves it into a released section below — in the same
commit as the dependency-pin bump, so the tag points at a commit whose
changelog already records the release (see D59, and the Releasing runbook in
[CLAUDE.md](CLAUDE.md)).

Releases before this file exists are recorded only in the git tags and in
[doc/DECISIONS.md](doc/DECISIONS.md).

## Unreleased

### common

### schema

### server

### client
