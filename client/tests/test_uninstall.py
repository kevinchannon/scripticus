import json
import os
import tempfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

from scripticus.cli import app
from scripticus.pack import pack_package
from scripticus.scaffold import scaffold_package

runner = CliRunner()


@pytest.fixture
def home(tmp_path, monkeypatch):
    home_dir = tmp_path / "scripticus-home"
    monkeypatch.setenv("SCRIPTICUS_HOME", str(home_dir))
    monkeypatch.chdir(tmp_path)
    return home_dir


def build_archive(
    parent: Path,
    name: str = "my-tool",
    namespace: str = "acme",
    extra_toml: str = "",
) -> Path:
    workdir = Path(tempfile.mkdtemp(dir=parent, prefix="pkgsrc-"))
    scaffold_package("python", name, namespace, workdir)
    manifest = workdir / name / "meta.toml"
    manifest.write_text(manifest.read_text() + extra_toml)
    return pack_package(workdir / name, parent / "archives")[0]


def install(archive: Path) -> None:
    result = runner.invoke(app, ["install", "-f", str(archive), "-y"])
    assert result.exit_code == 0, result.output


def shim_path(home: Path, command: str) -> Path:
    return home / "bin" / (f"{command}.cmd" if os.name == "nt" else command)


def lockfile(home: Path) -> dict:
    return json.loads((home / "installed.lock").read_text())


def test_uninstall_removes_files_shim_and_lockfile_entry(home, tmp_path):
    install(build_archive(tmp_path))

    result = runner.invoke(app, ["uninstall", "my-tool", "-y"])
    assert result.exit_code == 0, result.output
    assert "Uninstalled acme/my-tool 0.1.0" in result.output

    assert not shim_path(home, "my-tool").exists()
    assert not (home / "pkgs" / "acme").exists()
    assert lockfile(home)["packages"] == []


def test_namespaced_form_is_accepted(home, tmp_path):
    install(build_archive(tmp_path))

    result = runner.invoke(app, ["uninstall", "acme/my-tool", "-y"])
    assert result.exit_code == 0, result.output
    assert lockfile(home)["packages"] == []


def test_uninstalling_a_package_that_is_not_installed_is_an_error(home, tmp_path):
    result = runner.invoke(app, ["uninstall", "no-such-tool", "-y"])
    assert result.exit_code == 1
    assert "not installed" in result.output


def test_interactive_decline_removes_nothing(home, tmp_path):
    install(build_archive(tmp_path))

    result = runner.invoke(app, ["uninstall", "my-tool"], input="n\n")
    assert result.exit_code == 1
    assert "Aborted" in result.output
    assert shim_path(home, "my-tool").is_file()
    assert len(lockfile(home)["packages"]) == 1


def test_interactive_accept_removes(home, tmp_path):
    install(build_archive(tmp_path))

    result = runner.invoke(app, ["uninstall", "my-tool"], input="y\n")
    assert result.exit_code == 0, result.output
    assert not shim_path(home, "my-tool").exists()


def test_bare_name_matching_two_namespaces_is_an_error(home, tmp_path):
    install(build_archive(tmp_path, namespace="acme"))
    # A distinct command name, so the two same-named packages don't clash on shims.
    install(
        build_archive(
            tmp_path,
            namespace="globex",
            extra_toml='\n[commands]\nglobex-tool = "src/main.py"\n',
        )
    )

    result = runner.invoke(app, ["uninstall", "my-tool", "-y"])
    assert result.exit_code == 1
    assert "more than one" in result.output
    assert "acme/my-tool" in result.output
    assert "globex/my-tool" in result.output
    assert len(lockfile(home)["packages"]) == 2

    result = runner.invoke(app, ["uninstall", "globex/my-tool", "-y"])
    assert result.exit_code == 0, result.output
    assert [e["namespace"] for e in lockfile(home)["packages"]] == ["acme"]


def test_shims_taken_over_by_another_package_are_left_alone(home, tmp_path):
    # Same namespace, so tool-two took both conveniences (clash, acme.clash).
    clash = '\n[commands]\nclash = "src/main.py"\n'
    install(build_archive(tmp_path, name="tool-one", extra_toml=clash))
    result = runner.invoke(
        app,
        ["install", "-f", str(build_archive(tmp_path, name="tool-two", extra_toml=clash)), "--force", "all"],
    )
    assert result.exit_code == 0, result.output

    result = runner.invoke(app, ["uninstall", "tool-one", "-y"])
    assert result.exit_code == 0, result.output
    assert "No shared command shims to remove" in result.output

    # tool-two keeps the conveniences; only tool-one's fully-qualified shim went.
    assert "acme.tool-two.clash" in shim_path(home, "clash").read_text()
    assert not shim_path(home, "acme.tool-one.clash").exists()
    assert [e["name"] for e in lockfile(home)["packages"]] == ["tool-two"]


# --- Replacement discovery (D28) ------------------------------------------


def install_clash_providers(tmp_path, count: int) -> list[str]:
    """Install ``count`` packages in DISTINCT namespaces all providing a
    'clash' command; the last installed owns the bare shim. Distinct
    namespaces mean the namespaced shims (ns1.clash, ...) never collide, so
    only the bare `clash` is ever contested — one orphan on uninstall.
    Returns the package names."""
    clash = '\n[commands]\nclash = "src/main.py"\n'
    names = [f"tool-{n}" for n in range(1, count + 1)]
    for i, name in enumerate(names):
        archive = build_archive(tmp_path, name=name, namespace=f"ns{i + 1}", extra_toml=clash)
        args = ["install", "-f", str(archive)] + (["--force", "all"] if i else ["-y"])
        result = runner.invoke(app, args)
        assert result.exit_code == 0, result.output
    return names


def test_no_providers_means_no_replacement_output(home, tmp_path):
    install(build_archive(tmp_path))

    result = runner.invoke(app, ["uninstall", "my-tool", "-y"])
    assert result.exit_code == 0, result.output
    assert "provide" not in result.output
    assert "scripticus use" not in result.output


def test_single_replacement_is_hinted_with_yes(home, tmp_path):
    install_clash_providers(tmp_path, 2)

    result = runner.invoke(app, ["uninstall", "tool-2", "-y"])
    assert result.exit_code == 0, result.output
    assert "'clash' is also provided by ns1/tool-1" in result.output
    assert "scripticus use ns1/tool-1 clash" in result.output

    # Hinted only — never re-pointed silently.
    assert not shim_path(home, "clash").exists()
    # tool-1 keeps its own namespaced shim; it never owned the bare `clash`.
    [entry] = lockfile(home)["packages"]
    assert entry["shims"] == ["ns1.clash"]


def test_several_replacements_are_listed_with_yes(home, tmp_path):
    install_clash_providers(tmp_path, 3)

    result = runner.invoke(app, ["uninstall", "tool-3", "-y"])
    assert result.exit_code == 0, result.output
    assert "Several packages provide 'clash':" in result.output
    assert "ns1/tool-1" in result.output
    assert "ns2/tool-2" in result.output
    assert "No replacement selected by default" in result.output
    assert "scripticus use <namespace/name> clash" in result.output
    assert not shim_path(home, "clash").exists()


def test_picker_option_zero_leaves_command_without_a_shim(home, tmp_path):
    install_clash_providers(tmp_path, 2)

    result = runner.invoke(app, ["uninstall", "tool-2"], input="y\n0\n")
    assert result.exit_code == 0, result.output
    assert "0) No replacement" in result.output
    assert "1) ns1/tool-1" in result.output
    assert "left without a shim" in result.output

    assert not shim_path(home, "clash").exists()
    [entry] = lockfile(home)["packages"]
    assert entry["shims"] == ["ns1.clash"]


def test_picker_selects_the_only_replacement(home, tmp_path):
    install_clash_providers(tmp_path, 2)

    result = runner.invoke(app, ["uninstall", "tool-2"], input="y\n1\n")
    assert result.exit_code == 0, result.output
    assert "'clash' now points at ns1/tool-1" in result.output

    assert "ns1.tool-1.clash" in shim_path(home, "clash").read_text()
    [entry] = lockfile(home)["packages"]
    assert entry["name"] == "tool-1"
    assert entry["shims"] == ["clash", "ns1.clash"]


def test_picker_selects_among_several_replacements(home, tmp_path):
    install_clash_providers(tmp_path, 3)

    result = runner.invoke(app, ["uninstall", "tool-3"], input="y\n2\n")
    assert result.exit_code == 0, result.output
    assert "1) ns1/tool-1" in result.output
    assert "2) ns2/tool-2" in result.output
    assert "'clash' now points at ns2/tool-2" in result.output

    assert "ns2.tool-2.clash" in shim_path(home, "clash").read_text()
    by_name = {e["name"]: e for e in lockfile(home)["packages"]}
    assert "clash" in by_name["tool-2"]["shims"]
    assert "clash" not in by_name["tool-1"]["shims"]


def test_picker_rejects_out_of_range_numbers(home, tmp_path):
    install_clash_providers(tmp_path, 2)

    result = runner.invoke(app, ["uninstall", "tool-2"], input="y\n5\n1\n")
    assert result.exit_code == 0, result.output
    assert "between 0 and 1" in result.output
    assert "ns1.tool-1.clash" in shim_path(home, "clash").read_text()


def test_picker_default_is_no_replacement(home, tmp_path):
    install_clash_providers(tmp_path, 2)

    result = runner.invoke(app, ["uninstall", "tool-2"], input="y\n\n")
    assert result.exit_code == 0, result.output
    assert "left without a shim" in result.output
    assert not shim_path(home, "clash").exists()


def test_same_namespace_uninstall_orphans_both_convenience_tiers(home, tmp_path):
    """Two acme packages providing `clash`: the owner holds both `clash` and
    `acme.clash`, so uninstalling it offers a replacement for each."""
    clash = '\n[commands]\nclash = "src/main.py"\n'
    install(build_archive(tmp_path, name="tool-1", namespace="acme", extra_toml=clash))
    runner.invoke(
        app,
        ["install", "-f", str(build_archive(tmp_path, name="tool-2", namespace="acme", extra_toml=clash)), "--force", "all"],
    )

    # Re-point both orphaned shims onto tool-1 (two prompts, in sorted order).
    result = runner.invoke(app, ["uninstall", "tool-2"], input="y\n1\n1\n")
    assert result.exit_code == 0, result.output
    assert "'acme.clash' now points at acme/tool-1" in result.output
    assert "'clash' now points at acme/tool-1" in result.output

    [entry] = lockfile(home)["packages"]
    assert entry["shims"] == ["acme.clash", "clash"]
    assert "acme.tool-1.clash" in shim_path(home, "clash").read_text()
    assert "acme.tool-1.clash" in shim_path(home, "acme.clash").read_text()
