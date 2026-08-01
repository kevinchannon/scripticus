#!/usr/bin/env bats
# Publishing to an *organisation* namespace (D4), which is a materially
# different path from publishing to your own user namespace: `can_publish`
# short-circuits on `namespace == user` and never talks to Gitea, so the rest of
# the suite — which publishes under the test user — leaves all of this untested.
#
# Two things only bite here:
#
#   1. Membership. The publisher must belong to the organisation. The harness
#      puts them in a team with `Packages: Write` and nothing else: no owner
#      rights, no repositories. That is the least privilege that can publish,
#      and these tests are what say so.
#   2. The token's read:organization scope. `can_publish` asks Gitea about
#      membership using *the caller's own token*, so without that scope the
#      question cannot even be put — a failure that looks like a permissions
#      problem and is not.

load 'lib/helpers'

setup() {
    common_setup
}

@test "a team member with only Packages:Write publishes to the organisation" {
    local org="$SCRIPTICUS_E2E_ORG"
    local pkg
    pkg="$(unique_pkg)"

    register_remote
    author_org_package "$pkg" 0.1.0

    # The publisher is not an owner of $org and has no repositories in it.
    run env SCRIPTICUS_TOKEN="$SCRIPTICUS_E2E_ORG_TOKEN" \
        scripticus publish "builds/${pkg}-0.1.0"
    [ "$status" -eq 0 ]
    [[ "$output" == *"Published ${pkg} 0.1.0"* ]]

    # Published under the organisation, not under the publisher's own name.
    run scripticus list --available "${org}/${pkg}"
    [ "$status" -eq 0 ]
    [[ "$output" == *"${org}/${pkg}"* ]]
    [[ "$output" != *"$SCRIPTICUS_E2E_ORG_USER/${pkg}"* ]]

    # Install it back. Not a repeat of the lifecycle arc: the blob pointer is
    # namespace-scoped, so this is the one assertion that the round trip works
    # for an organisation — the index listing above would pass either way.
    run do_login
    [ "$status" -eq 0 ]
    run scripticus install "${org}/${pkg}" --yes
    [ "$status" -eq 0 ]
    run "$pkg"
    [ "$status" -eq 0 ]
    [[ "$output" == *"Hello from ${pkg}!"* ]]
}

@test "publishing to an organisation without read:organization is refused" {
    local org="$SCRIPTICUS_E2E_ORG"
    local pkg
    pkg="$(unique_pkg)"

    register_remote
    author_org_package "$pkg" 0.1.0

    # Same user, same team, same permissions as the passing test above — the
    # only difference is the token's scopes. Everything about the organisation
    # is already correct, which is exactly what makes this failure misleading.
    run env SCRIPTICUS_TOKEN="$SCRIPTICUS_E2E_ORG_NARROW_TOKEN" \
        scripticus publish "builds/${pkg}-0.1.0"
    [ "$status" -ne 0 ]
    # Keep the refusal: the assertions below run after another `run`, which
    # replaces $output.
    local refusal="$output"

    # Nothing was published: the batch is atomic (D37), so a refusal at the ACL
    # check must leave no version, no blob, and nothing in the index.
    run scripticus list --available "${org}/${pkg}"
    [[ "$output" != *"${org}/${pkg}"* ]]

    # Deliberately loose on the message. Today it reads "'<user>' cannot publish
    # to namespace '<org>'", which names the wrong cause: `can_publish` collapses
    # Gitea's scope refusal into a plain "not permitted" (gitea.py). When that is
    # fixed to name the missing scope, tighten this to assert `read:organization`
    # appears — the point of this test is that the fix has somewhere to land.
    [[ "$refusal" == *"$SCRIPTICUS_E2E_ORG_USER"* ]]
    [[ "$refusal" == *"${org}"* ]]
}

@test "the same under-scoped token still publishes to its own namespace" {
    # Proves the previous failure is about the organisation lookup and not a
    # broken token: write:package + read:user is everything a user-namespace
    # publish needs, which is why the rest of the suite never notices.
    local user="$SCRIPTICUS_E2E_ORG_USER"
    local pkg
    pkg="$(unique_pkg)"

    register_remote
    scripticus new bash "$pkg" -n "$user"
    scripticus pack "$pkg" -o builds

    run env SCRIPTICUS_TOKEN="$SCRIPTICUS_E2E_ORG_NARROW_TOKEN" \
        scripticus publish "builds/${pkg}-0.1.0"
    [ "$status" -eq 0 ]
    [[ "$output" == *"Published ${pkg} 0.1.0"* ]]
}
