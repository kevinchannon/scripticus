"""Authoring, installing, and reading snippets (D58): `new --snippet`, `pack`,
`install -f`, and `snip` itself."""

import json
import tempfile
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

import scripticus.remote_install as remote_install
from scripticus import clipboard
from scripticus.cli import app
from scripticus.config import Remote, save_remotes
from scripticus.credentials import set_token
from scripticus.pack import pack_package
from scripticus_common.treehash import tree_hash

URL = "https://reg.example.com"

# Click keeps the two streams apart, which is what these tests turn on: the
# snippet goes to stdout and everything *about* it goes to stderr.
runner = CliRunner()

TRAP_SH = "trap 'rm -f \"$tmp\"' EXIT\n"


@pytest.fixture
def home(tmp_path, monkeypatch):
    home_dir = tmp_path / "scripticus-home"
    monkeypatch.setenv("SCRIPTICUS_HOME", str(home_dir))
    monkeypatch.chdir(tmp_path)
    return home_dir


def author_snippet_package(
    parent: Path,
    name: str = "boilerplate",
    namespace: str = "acme",
    snippets: dict[str, str] | None = None,
) -> Path:
    """Write a snippet package with the given ``{"args.sh": body}`` variants;
    return its directory."""
    snippets = snippets or {"trap.sh": TRAP_SH}
    workdir = Path(tempfile.mkdtemp(dir=parent, prefix="pkgsrc-"))
    package_dir = workdir / name
    (package_dir / "src").mkdir(parents=True)

    declared = sorted({filename.rsplit(".", 1)[0] for filename in snippets})
    manifest = [
        "[package]",
        f'namespace = "{namespace}"',
        f'name = "{name}"',
        'version = "0.1.0"',
        'description = "Boilerplate"',
    ]
    for snippet in declared:
        manifest += ["", f"[snippet.{snippet}]", f'description = "{snippet} boilerplate"']
    (package_dir / "meta.toml").write_text("\n".join(manifest) + "\n")
    for filename, body in snippets.items():
        (package_dir / "src" / filename).write_text(body)
    return package_dir


def build_snippet_archive(parent: Path, **kwargs) -> Path:
    """Author and pack a snippet package; return the .tar.gz."""
    package_dir = author_snippet_package(parent, **kwargs)
    return next(
        archive
        for archive in pack_package(package_dir, parent / "archives")
        if archive.name.endswith(".tar.gz")
    )


def install(archive: Path):
    return runner.invoke(app, ["install", "-f", str(archive), "-y"])


def lockfile(home: Path) -> dict:
    return json.loads((home / "installed.lock").read_text())


# --- Authoring --------------------------------------------------------------


def test_new_snippet_scaffolds_a_valid_packable_package(home, tmp_path):
    result = runner.invoke(app, ["new", "--snippet", "argparse", "-n", "acme"])
    assert result.exit_code == 0, result.output

    package_dir = tmp_path / "argparse"
    assert (package_dir / "src" / "argparse.sh").is_file()
    assert not (package_dir / "test").exists()  # nothing to run, nothing to test

    # The proof it is valid is that pack accepts it — pack validates the
    # manifest against the tree.
    [*_] = pack_package(package_dir, tmp_path / "out")


def test_new_snippet_takes_the_extension_for_its_first_variant(home, tmp_path):
    result = runner.invoke(
        app, ["new", "--snippet", "argparse", "-n", "acme", "--ext", "cpp"]
    )
    assert result.exit_code == 0, result.output
    # A language Scripticus cannot run as a command still gets a snippet.
    assert (tmp_path / "argparse" / "src" / "argparse.cpp").is_file()


def test_new_snippet_rejects_a_language(home, tmp_path):
    result = runner.invoke(app, ["new", "--snippet", "bash", "argparse", "-n", "acme"])
    assert result.exit_code == 1
    assert "has no language" in result.output


def test_new_without_language_or_snippet_names_both_forms(home, tmp_path):
    result = runner.invoke(app, ["new", "my-tool", "-n", "acme"])
    assert result.exit_code == 1
    assert "missing LANGUAGE or NAME" in result.output
    assert "--snippet" in result.output


def test_snippet_package_packs_with_the_any_platform_tag(home, tmp_path):
    archive = build_snippet_archive(tmp_path)
    # Wheel-style: no platform, no language of its own (D58).
    assert archive.name == "boilerplate-0.1.0-any-snippet.tar.gz"
    # Still one archive per format group, so Windows gets its native container.
    assert (archive.parent / "boilerplate-0.1.0-any-snippet.zip").is_file()


# --- Installing -------------------------------------------------------------


def test_installing_a_snippet_package_creates_no_shims(home, tmp_path):
    assert install(build_snippet_archive(tmp_path)).exit_code == 0

    assert (home / "pkgs" / "acme" / "boilerplate" / "0.1.0" / "src" / "trap.sh").is_file()
    assert not any((home / "bin").glob("*"))

    [entry] = lockfile(home)["packages"]
    assert entry["commands"] == []
    assert entry["shims"] == []
    assert entry["snippets"] == {"trap": ["sh"]}
    assert entry["language"] == "snippet"


def test_remote_install_records_the_derived_snippets(home, tmp_path, monkeypatch):
    package_dir = author_snippet_package(tmp_path, snippets={"args.sh": "# sh\n"})
    archive = next(
        a
        for a in pack_package(package_dir, tmp_path / "archives")
        if a.name.endswith(".tar.gz")
    )
    pointer = "/api/packages/acme/generic/boilerplate/0.1.0/" + archive.name
    save_remotes(home, [Remote("origin", URL)])
    set_token(home, URL, "tok")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/resolve":
            # A snippet package resolves like any other; its command map is
            # simply empty (D58).
            return httpx.Response(
                200,
                json={
                    "packages": [
                        {
                            "namespace": "acme",
                            "name": "boilerplate",
                            "version": "0.1.0",
                            "content_hash": tree_hash(package_dir),
                            "download_pointer": pointer,
                            "direct": True,
                            "already_satisfied": False,
                            "commands": {},
                        }
                    ],
                    "tools": [],
                },
            )
        return httpx.Response(200, content=archive.read_bytes())

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        remote_install, "_client", lambda: httpx.Client(transport=transport)
    )

    result = runner.invoke(app, ["install", "acme/boilerplate", "-y"])
    assert result.exit_code == 0, result.output

    # The variants are derived client-side from the staged tree, so the remote
    # path lands the same lockfile entry the local one does.
    [entry] = lockfile(home)["packages"]
    assert entry["snippets"] == {"args": ["sh"]}

    assert runner.invoke(app, ["snip", "args.sh"]).stdout == "# sh\n"


# --- Reading ----------------------------------------------------------------


def test_snip_prints_the_snippet_verbatim_on_stdout(home, tmp_path):
    install(build_snippet_archive(tmp_path))

    result = runner.invoke(app, ["snip", "trap.sh"])
    assert result.exit_code == 0
    # Byte-for-byte: this is about to be pasted into someone's script, so it
    # must not be highlighted, wrapped, or reformatted.
    assert result.stdout == TRAP_SH
    assert result.stderr == ""


def test_snip_appends_a_final_newline_so_composition_is_safe(home, tmp_path):
    install(build_snippet_archive(tmp_path, snippets={"trap.sh": "trap cleanup EXIT"}))

    result = runner.invoke(app, ["snip", "trap.sh"])
    assert result.stdout == "trap cleanup EXIT\n"


def test_bare_name_collapses_when_there_is_only_one_variant(home, tmp_path):
    install(build_snippet_archive(tmp_path))

    result = runner.invoke(app, ["snip", "trap"])
    assert result.exit_code == 0
    assert result.stdout == TRAP_SH


def test_bare_name_lists_the_variants_rather_than_guessing(home, tmp_path):
    install(
        build_snippet_archive(
            tmp_path, snippets={"args.sh": "# sh\n", "args.py": "# py\n"}
        )
    )

    result = runner.invoke(app, ["snip", "args"])
    assert result.exit_code == 1
    # The listing is a diagnostic: it must not reach a redirected stdout.
    assert result.stdout == ""
    assert "ambiguous" in result.stderr
    assert "acme/boilerplate:args.py" in result.stderr
    assert "acme/boilerplate:args.sh" in result.stderr


def test_two_packages_providing_one_token_list_rather_than_last_wins(home, tmp_path):
    install(build_snippet_archive(tmp_path, name="boilerplate"))
    install(build_snippet_archive(tmp_path, name="other-boilerplate"))

    result = runner.invoke(app, ["snip", "trap.sh"])
    assert result.exit_code == 1
    assert "acme/boilerplate:trap.sh" in result.stderr
    assert "acme/other-boilerplate:trap.sh" in result.stderr

    # ...and the qualified form is how you pick one (D46's fully-namespaced
    # reference, the documented escape hatch).
    result = runner.invoke(app, ["snip", "acme/other-boilerplate:trap.sh"])
    assert result.exit_code == 0
    assert result.stdout == TRAP_SH


def test_wrong_extension_names_the_ones_that_exist(home, tmp_path):
    install(build_snippet_archive(tmp_path, snippets={"args.sh": "# sh\n"}))

    result = runner.invoke(app, ["snip", "args.py"])
    assert result.exit_code == 1
    assert "args.sh" in result.stderr


def test_unknown_snippet_is_an_error_on_stderr(home, tmp_path):
    result = runner.invoke(app, ["snip", "nope.sh"])
    assert result.exit_code == 1
    assert result.stdout == ""
    assert "no snippet 'nope.sh' is installed" in result.stderr


def test_bare_reference_must_be_fully_namespaced(home, tmp_path):
    result = runner.invoke(app, ["snip", "boilerplate:trap.sh"])
    assert result.exit_code == 1
    assert "fully namespaced" in result.stderr


# --- Copying ----------------------------------------------------------------


def test_copy_tees_to_the_clipboard_and_still_prints(home, tmp_path, monkeypatch):
    install(build_snippet_archive(tmp_path))
    copied = {}
    monkeypatch.setattr(
        clipboard, "copy", lambda text: copied.setdefault("text", text) and "pbcopy"
    )

    result = runner.invoke(app, ["snip", "trap.sh", "-c"])
    assert result.exit_code == 0
    assert result.stdout == TRAP_SH  # a pipe could copy or show, not both
    assert copied["text"] == TRAP_SH


def test_copy_without_a_clipboard_warns_but_still_succeeds(home, tmp_path, monkeypatch):
    install(build_snippet_archive(tmp_path))
    monkeypatch.setattr(clipboard, "copy", lambda text: None)

    result = runner.invoke(app, ["snip", "trap.sh", "--copy"])
    # The degradation rule: -c must never cost you the snippet (D58).
    assert result.exit_code == 0
    assert result.stdout == TRAP_SH
    assert "not copied" in result.stderr


# --- Listing ----------------------------------------------------------------


def test_installed_listing_marks_a_snippet_package(home, tmp_path):
    install(build_snippet_archive(tmp_path))

    result = runner.invoke(app, ["list", "--installed"])
    assert result.exit_code == 0
    assert "acme/boilerplate" in result.stdout
    assert "snippet" in result.stdout
