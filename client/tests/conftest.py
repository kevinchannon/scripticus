"""Shared fixtures for the remote-install / update CLI tests.

Under pytest's importlib import mode sibling test modules cannot import each
other, so the fake-server harness both suites need is exposed here as
fixtures (the import-mode-agnostic sharing mechanism)."""

import json
import os
import tempfile
from pathlib import Path

import httpx
import pytest

import scripticus.remote_install as remote_install
from scripticus.pack import pack_package
from scripticus.scaffold import scaffold_package
from scripticus_common.treehash import tree_hash

REG_URL = "https://reg.example.com"


@pytest.fixture
def home(tmp_path, monkeypatch):
    home_dir = tmp_path / "scripticus-home"
    monkeypatch.setenv("SCRIPTICUS_HOME", str(home_dir))
    monkeypatch.delenv("SCRIPTICUS_TOKEN", raising=False)
    return home_dir


@pytest.fixture
def make_package(tmp_path):
    """Factory: scaffold + pack a real package, returning
    (tar.gz path, content hash, download pointer)."""

    def _make(name, namespace="acme", version="0.1.0", language="python", extra_toml=""):
        src_parent = Path(tempfile.mkdtemp(dir=tmp_path))
        scaffold_package(language, name, namespace, src_parent)
        pkg_dir = src_parent / name
        manifest = pkg_dir / "meta.toml"
        manifest.write_text(
            manifest.read_text().replace('version = "0.1.0"', f'version = "{version}"')
            + extra_toml
        )
        archive = next(
            a for a in pack_package(pkg_dir, tmp_path / "archives")
            if a.name.endswith(".tar.gz")
        )
        pointer = f"/api/packages/{namespace}/generic/{name}/{version}/{archive.name}"
        return archive, tree_hash(pkg_dir), pointer

    return _make


@pytest.fixture
def fake_server(monkeypatch):
    """Factory: route /resolve to a handler and blob GETs to a path->bytes map,
    returning the recorded request list."""

    def _serve(resolve_handler, blobs=None):
        blobs = blobs or {}
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if request.url.path == "/resolve":
                return resolve_handler(request)
            body = blobs.get(request.url.path)
            return httpx.Response(200, content=body) if body is not None else httpx.Response(404)

        transport = httpx.MockTransport(handler)
        monkeypatch.setattr(
            remote_install, "_client", lambda: httpx.Client(transport=transport)
        )
        return requests

    return _serve


@pytest.fixture
def resolved_pkg():
    def _pkg(namespace, name, version, content_hash, pointer, **kw):
        return {
            "namespace": namespace,
            "name": name,
            "version": version,
            "content_hash": content_hash,
            "download_pointer": pointer,
            "direct": kw.get("direct", True),
            "already_satisfied": kw.get("already_satisfied", False),
            "commands": kw.get("commands", {name: "src/main.py"}),
        }

    return _pkg


@pytest.fixture
def lockfile():
    def _lock(home: Path) -> list[dict]:
        return json.loads((home / "installed.lock").read_text())["packages"]

    return _lock


@pytest.fixture
def shim_path():
    def _path(home: Path, command: str) -> Path:
        return home / "bin" / (f"{command}.cmd" if os.name == "nt" else command)

    return _path
