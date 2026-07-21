import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from scripticus_schema.resolve_api import InstalledPackage
from scripticus_server import db
from scripticus_server.app import app, get_session
from scripticus_server.resolve import ResolutionError, resolve_closure


# --- Fake index for solver unit tests --------------------------------------


class FakeArtifact:
    def __init__(self, content_hash: str, download_pointer: str):
        self.content_hash = content_hash
        self.download_pointer = download_pointer


class FakeIndex:
    def __init__(self):
        self._pkgs: dict[str, dict[str, dict]] = {}

    def add(
        self,
        package,
        version,
        platforms=("linux",),
        deps=None,
        tools=None,
        commands=None,
        yanked=False,
    ):
        self._pkgs.setdefault(package, {})[version] = {
            "platforms": set(platforms),
            "deps": deps or {},
            "tools": tools or {},
            "commands": commands or {},
            "yanked": yanked,
        }
        return self

    def exists(self, package):
        return package in self._pkgs

    def candidates(self, package, platform):
        out = {}
        for version, meta in self._pkgs.get(package, {}).items():
            if not meta["yanked"] and platform in meta["platforms"]:
                out[version] = FakeArtifact(
                    f"sha256:{package}:{version}", f"/blob/{package}/{version}"
                )
        return out

    def dependencies(self, package, version):
        meta = self._pkgs.get(package, {}).get(version)
        return list(meta["deps"].items()) if meta else []

    def tools(self, package, version):
        meta = self._pkgs.get(package, {}).get(version)
        return list(meta["tools"].items()) if meta else []

    def commands(self, package, version):
        meta = self._pkgs.get(package, {}).get(version)
        return dict(meta["commands"]) if meta else {}


def resolve(index, root, spec="", platform="linux", installed=None):
    return resolve_closure(
        index,
        root,
        spec,
        platform,
        [InstalledPackage(package=p, version=v) for p, v in (installed or {}).items()],
    )


def versions_of(result):
    return {f"{p.namespace}/{p.name}": p.version for p in result.packages}


# --- Version selection ------------------------------------------------------


def test_resolves_latest_release_by_default():
    index = FakeIndex().add("a/foo", "1.0.0").add("a/foo", "1.2.0")
    result = resolve(index, "a/foo")
    assert versions_of(result) == {"a/foo": "1.2.0"}
    assert result.packages[0].direct is True
    assert result.packages[0].already_satisfied is False
    assert result.packages[0].content_hash == "sha256:a/foo:1.2.0"
    assert result.packages[0].download_pointer == "/blob/a/foo/1.2.0"


def test_respects_the_version_spec():
    index = FakeIndex().add("a/foo", "1.2.0").add("a/foo", "2.0.0")
    assert versions_of(resolve(index, "a/foo", "^1.0")) == {"a/foo": "1.2.0"}


# --- Closure, ordering, consolidation --------------------------------------


def test_transitive_closure_is_ordered_deps_first():
    index = (
        FakeIndex()
        .add("a/foo", "1.0.0", deps={"a/bar": "^1"})
        .add("a/bar", "1.0.0", deps={"a/baz": "^1"})
        .add("a/baz", "1.0.0")
    )
    result = resolve(index, "a/foo")
    ordered = [f"{p.namespace}/{p.name}" for p in result.packages]
    assert ordered == ["a/baz", "a/bar", "a/foo"]
    assert [p.direct for p in result.packages] == [False, False, True]


def test_diamond_consolidates_to_one_highest_satisfying_version():
    index = (
        FakeIndex()
        .add("a/root", "1.0.0", deps={"a/x": "^1", "a/y": "^1"})
        .add("a/x", "1.0.0", deps={"a/c": "^1"})
        .add("a/y", "1.0.0", deps={"a/c": ">=1.1"})
        .add("a/c", "1.0.0")
        .add("a/c", "1.1.0")
        .add("a/c", "1.2.0")
    )
    result = resolve(index, "a/root")
    assert versions_of(result)["a/c"] == "1.2.0"
    # exactly one node for c
    assert sum(p.name == "c" for p in result.packages) == 1


def test_conflicting_windows_raise_naming_the_package():
    index = (
        FakeIndex()
        .add("a/root", "1.0.0", deps={"a/x": "^1", "a/y": "^1"})
        .add("a/x", "1.0.0", deps={"a/c": "^1"})
        .add("a/y", "1.0.0", deps={"a/c": "^2"})
        .add("a/c", "1.5.0")
        .add("a/c", "2.0.0")
    )
    with pytest.raises(ResolutionError, match="a/c"):
        resolve(index, "a/root")


def test_backtracks_past_a_false_conflict():
    # root needs a ^1 and b ^1; a@1.1 forces b ^2 (a dead end), but a@1.0
    # needs only b ^1 — the solver must back off a to find the solution.
    index = (
        FakeIndex()
        .add("a/root", "1.0.0", deps={"a/a": "^1", "a/b": "^1"})
        .add("a/a", "1.0.0", deps={"a/b": "^1"})
        .add("a/a", "1.1.0", deps={"a/b": "^2"})
        .add("a/b", "1.0.0")
        .add("a/b", "2.0.0")
    )
    result = resolve(index, "a/root")
    assert versions_of(result) == {"a/root": "1.0.0", "a/a": "1.0.0", "a/b": "1.0.0"}


# --- Installed state --------------------------------------------------------


def test_prefers_an_installed_version_that_still_satisfies():
    index = (
        FakeIndex().add("a/foo", "1.0.0").add("a/foo", "1.1.0").add("a/foo", "1.2.0")
    )
    result = resolve(index, "a/foo", "^1", installed={"a/foo": "1.1.0"})
    assert versions_of(result) == {"a/foo": "1.1.0"}  # not bumped to 1.2.0
    assert result.packages[0].already_satisfied is True


def test_installed_dependent_constrains_the_closure():
    # app@1.0 (installed, not in the new closure) needs lib <2.0; installing
    # lib must therefore not pick 2.0 even though it is the latest.
    index = (
        FakeIndex()
        .add("a/app", "1.0.0", deps={"a/lib": "<2.0.0"})
        .add("a/lib", "1.5.0")
        .add("a/lib", "2.0.0")
    )
    result = resolve(index, "a/lib", installed={"a/app": "1.0.0"})
    assert versions_of(result) == {"a/lib": "1.5.0"}


def test_installed_but_unknown_package_does_not_constrain():
    # A locally-installed package the index never published contributes no
    # constraints (D33 — it cannot depend on anything here anyway).
    index = FakeIndex().add("a/lib", "2.0.0")
    result = resolve(index, "a/lib", installed={"local/thing": "9.9.9"})
    assert versions_of(result) == {"a/lib": "2.0.0"}


# --- Platform, yank, tools --------------------------------------------------


def test_missing_platform_variant_is_an_error():
    index = FakeIndex().add("a/foo", "1.0.0", platforms=("linux",))
    with pytest.raises(ResolutionError, match="windows"):
        resolve(index, "a/foo", platform="windows")


def test_yanked_versions_are_excluded():
    index = FakeIndex().add("a/foo", "1.0.0").add("a/foo", "1.1.0", yanked=True)
    assert versions_of(resolve(index, "a/foo")) == {"a/foo": "1.0.0"}


def test_commands_are_returned_per_package():
    index = FakeIndex().add(
        "a/foo", "1.0.0", commands={"foo": "src/main.py", "foo-helper": "src/helper.py"}
    )
    result = resolve(index, "a/foo")
    assert result.packages[0].commands == {
        "foo": "src/main.py",
        "foo-helper": "src/helper.py",
    }


def test_tools_are_aggregated_required_winning():
    index = (
        FakeIndex()
        .add("a/foo", "1.0.0", deps={"a/bar": "^1"}, tools={"jq": True})
        .add("a/bar", "1.0.0", tools={"jq": False, "fzf": False})
    )
    result = resolve(index, "a/foo")
    tools = {t.name: t.required for t in result.tools}
    assert tools == {"jq": True, "fzf": False}


# --- The endpoint over a seeded DB -----------------------------------------


@pytest.fixture
def client(session_factory):
    def override():
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override
    yield TestClient(app)
    app.dependency_overrides.clear()


def seed(session_factory, namespace, name, version, **kwargs):
    deps = kwargs.get("deps", {})
    tools = kwargs.get("tools", {})
    commands = kwargs.get("commands", {})
    platforms = kwargs.get("platforms", ("linux",))
    with session_factory() as session:
        ns = session.scalar(
            select(db.Namespace).where(db.Namespace.name == namespace)
        ) or db.Namespace(name=namespace)
        package = session.scalar(
            select(db.Package)
            .join(db.Namespace)
            .where(db.Namespace.name == namespace, db.Package.name == name)
        ) or db.Package(namespace=ns, name=name)
        pv = db.PackageVersion(package=package, version=version)
        db.Artifact(
            package_version=pv,
            platforms=",".join(platforms),
            language="bash",
            content_hash=f"sha256:{name}:{version}",
            gitea_pointer=f"/blob/{namespace}/{name}/{version}",
        )
        for target, spec in deps.items():
            pv.dependencies.append(db.Dependency(target=target, spec=spec))
        for tool, required in tools.items():
            pv.tool_deps.append(db.ToolDep(name=tool, required=required))
        for command, script in commands.items():
            pv.commands.append(db.Command(name=command, script_path=script))
        session.add(package)
        session.add(pv)
        session.commit()


def post_resolve(client, root, spec="", platform="linux", installed=None):
    return client.post(
        "/resolve",
        json={
            "root": root,
            "spec": spec,
            "platform": platform,
            "installed": installed or [],
        },
    )


def test_endpoint_resolves_closure_with_pointers(client, session_factory):
    seed(
        session_factory,
        "infra",
        "log-common",
        "2.1.0",
        tools={"jq": True},
        commands={"log-common": "src/main.sh"},
    )
    seed(session_factory, "infra", "backup", "1.0.0", deps={"infra/log-common": "^2.0"})

    response = post_resolve(client, "infra/backup")
    assert response.status_code == 200, response.text
    body = response.json()
    names = [f"{p['namespace']}/{p['name']}@{p['version']}" for p in body["packages"]]
    assert names == ["infra/log-common@2.1.0", "infra/backup@1.0.0"]
    log_common = body["packages"][0]
    assert log_common["download_pointer"] == "/blob/infra/log-common/2.1.0"
    assert log_common["content_hash"] == "sha256:log-common:2.1.0"
    assert log_common["commands"] == {"log-common": "src/main.sh"}
    assert body["packages"][1]["direct"] is True
    assert body["tools"] == [{"name": "jq", "required": True}]


def test_endpoint_unknown_root_is_404(client):
    assert post_resolve(client, "infra/nope").status_code == 404


def test_endpoint_conflict_is_422(client, session_factory):
    seed(session_factory, "a", "root", "1.0.0", deps={"a/x": "^1", "a/y": "^1"})
    seed(session_factory, "a", "x", "1.0.0", deps={"a/c": "^1"})
    seed(session_factory, "a", "y", "1.0.0", deps={"a/c": "^2"})
    seed(session_factory, "a", "c", "1.0.0")
    seed(session_factory, "a", "c", "2.0.0")

    response = post_resolve(client, "a/root")
    assert response.status_code == 422
    assert "a/c" in response.json()["detail"]


def test_endpoint_honours_installed_from_the_request_body(client, session_factory):
    seed(session_factory, "a", "app", "1.0.0", deps={"a/lib": "<2.0.0"})
    seed(session_factory, "a", "lib", "1.5.0")
    seed(session_factory, "a", "lib", "2.0.0")

    response = post_resolve(
        client, "a/lib", installed=[{"package": "a/app", "version": "1.0.0"}]
    )
    assert response.status_code == 200, response.text
    assert response.json()["packages"][0]["version"] == "1.5.0"
