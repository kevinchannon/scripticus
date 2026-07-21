import shutil
from enum import Enum
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.markup import escape

from scripticus import __version__, scaffold
from scripticus.config import ConfigError, Tools, load_remotes, load_tools, save_remotes
from scripticus.credentials import CredentialsError, resolve_token, set_token
from scripticus.init import ensure_persistent_path, ensure_skeleton, on_path
from scripticus.install import (
    InstallError,
    Transaction,
    apply_install,
    current_os,
    prepare_install,
    read_lockfile,
    scripticus_home,
)
from scripticus.login import LoginError, prepare_login
from scripticus.remote_install import (
    RemoteInstallError,
    RemotePlan,
    apply_remote,
    build_plan,
    installed_closure,
    parse_target,
    resolve_root,
    stage_downloads,
)
from scripticus.tools import ToolError, install_missing_required
from scripticus.whoami import WhoAmIError, verify_token
from scripticus_schema.manifest import ManifestError
from scripticus.pack import PackError, pack_package
from scripticus.publish import (
    PublishError,
    matching_archives,
    publish_archives,
    resolve_remote,
)
from scripticus.uninstall import (
    Candidate,
    UninstallError,
    apply_uninstall,
    find_installed,
    find_replacements,
    install_replacement,
)
from scripticus.use import UseError, prepare_use

app = typer.Typer(no_args_is_help=True)
console = Console()


def _print_version(value: bool) -> None:
    if value:
        console.print(f"scripticus {__version__}")
        raise typer.Exit()


@app.callback(invoke_without_command=True)
def main(
    version: bool = typer.Option(
        False,
        "--version",
        "-v",
        callback=_print_version,
        is_eager=True,
        help="Show the version and exit.",
    ),
) -> None:
    """Scripticus — publish, discover, version, and install shared scripts."""


@app.command()
def init() -> None:
    """One-time setup: create ~/.scripticus and put its bin dir on PATH."""
    home = scripticus_home()
    bin_dir = home / "bin"

    if ensure_skeleton(home):
        console.print(f"Created {home} (bin/)")

    if on_path(bin_dir):
        console.print(f"{bin_dir} is already on your PATH — nothing more to do")
        return

    changed, where = ensure_persistent_path(bin_dir)
    if changed:
        console.print(f"Added {bin_dir} to PATH in {where}")
    else:
        console.print(f"{bin_dir} is already configured in {where}")
    console.print("Restart your shell (or re-source your profile) to pick it up.")


def _validate_language(value: str) -> str:
    if value not in scaffold.LANGUAGES:
        supported = ", ".join(sorted(scaffold.LANGUAGES))
        raise typer.BadParameter(f"unknown language '{value}' (supported: {supported})")
    return value


def _validate_package_name(value: str) -> str:
    if not scaffold.PACKAGE_NAME_RE.match(value):
        raise typer.BadParameter(
            f"'{value}' is not a valid package name"
            " (names are kebab-case: lower-case letters, digits, and dashes)"
        )
    return value


def _validate_namespace(value: str) -> str:
    if not scaffold.NAMESPACE_RE.match(value):
        raise typer.BadParameter(
            f"'{value}' is not a valid namespace"
            " (lower-case letters, digits, and dashes, starting with a letter)"
        )
    return value


@app.command()
def new(
    language: str = typer.Argument(
        ...,
        callback=_validate_language,
        help="Language of the new package (bash, powershell, python).",
    ),
    name: str = typer.Argument(
        ...,
        callback=_validate_package_name,
        help="Package name (kebab-case).",
    ),
    namespace: str = typer.Option(
        ...,
        "--namespace",
        "-n",
        callback=_validate_namespace,
        help="Publishing namespace (a Gitea user or organisation).",
    ),
) -> None:
    """Scaffold a new package directory."""
    cwd = Path.cwd()
    try:
        created = scaffold.scaffold_package(language, name, namespace, cwd)
    except scaffold.ScaffoldError as exc:
        console.print(f"[red]error:[/red] {escape(str(exc))}")
        raise typer.Exit(code=1) from exc

    console.print(f"Created package [bold]{name}[/bold]:")
    for path in created:
        suffix = "/" if path.is_dir() else ""
        console.print(f"  {path.relative_to(cwd)}{suffix}")


@app.command()
def pack(
    package_dir: Path = typer.Argument(
        ...,
        exists=True,
        file_okay=False,
        help="Path to the package directory to archive.",
    ),
    output: Path = typer.Option(
        Path("."),
        "--output",
        "-o",
        file_okay=False,
        help="Directory to place the archive in (created if needed).",
    ),
) -> None:
    """Archive a package directory into a distributable artifact."""
    try:
        archive_paths = pack_package(package_dir, output)
    except (ManifestError, PackError) as exc:
        console.print(f"[red]error:[/red] {escape(str(exc))}")
        raise typer.Exit(code=1) from exc

    console.print(f"Packed [bold]{package_dir.name}[/bold]:")
    for archive_path in archive_paths:
        console.print(f"  {archive_path}")


class ForceMode(str, Enum):
    NO_CONFLICTS = "no-conflicts"
    ALL = "all"


def _print_transaction(transaction: Transaction) -> None:
    console.print(
        f"Installing [bold]{transaction.package_id}[/bold] {transaction.version}"
        f" (from {transaction.source})"
    )

    if transaction.action == "install":
        console.print("\nNew packages:")
        commands = ", ".join(sorted(transaction.commands))
        console.print(f"  {transaction.package_id}  {transaction.version}  (commands: {commands})")
    elif transaction.action == "reinstall":
        console.print(
            f"\nReinstalling {transaction.package_id} {transaction.version}"
            " (contents differ from the installed copy)"
        )
    else:
        marker = "  (downgrade!)" if transaction.action == "downgrade" else ""
        console.print("\nVersion changes:")
        console.print(
            f"  {transaction.package_id}  {transaction.installed_version}"
            f" -> {transaction.version}{marker}"
        )

    def tool_line(kind: str, tools, missing_note: str) -> None:
        if tools:
            names = ", ".join(t.name for t in tools)
            missing = [t.name for t in tools if not t.found]
            status = f"[NOT FOUND: {', '.join(missing)}{missing_note}]" if missing else "[found]"
            console.print(f"{kind} system tools: {names}        {status}")

    console.print()
    tool_line("Required", transaction.required_tools, "")
    tool_line("Optional", transaction.optional_tools, " — some features degraded")

    if transaction.conflicts:
        console.print("\nShim conflicts:")
        for conflict in transaction.conflicts:
            console.print(
                f"  {conflict.shim}  currently owned by {conflict.owner}"
                " — will be overwritten"
            )


@app.command()
def install(
    package: Optional[str] = typer.Argument(
        None,
        help="Package to install from a remote, as 'namespace/name' or"
        " 'namespace/name@<version>' (a full name, not bare — D46).",
    ),
    file: Optional[Path] = typer.Option(
        None,
        "--file",
        "-f",
        exists=True,
        dir_okay=False,
        help="Install from a local package archive instead of a remote.",
    ),
    remote: Optional[str] = typer.Option(
        None,
        "--remote",
        help="Force a specific remote (default: the first one hosting the package).",
    ),
    skip_tools: bool = typer.Option(
        False,
        "--skip-tools",
        help="Skip the system-tool check and installation entirely.",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Auto-accept the transaction, but abort entirely on any shim conflict"
        " (same as --force no-conflicts).",
    ),
    force: Optional[ForceMode] = typer.Option(
        None,
        "--force",
        help="no-conflicts: auto-accept but abort on shim conflicts."
        " all: auto-accept everything, reporting each overwritten shim.",
    ),
) -> None:
    """Install a package from a remote (namespace/name[@version]) or a local
    archive (-f)."""
    if (package is None) == (file is None):
        console.print(
            "[red]error:[/red] give a package name to install from a remote,"
            " or -f <archive> to install a local file (not both)"
        )
        raise typer.Exit(code=1)

    mode = force.value if force else ("no-conflicts" if yes else None)
    if file is not None:
        _install_local(file, mode)
    else:
        _install_remote(package, remote, skip_tools, mode)


def _install_local(file: Path, mode: Optional[str]) -> None:
    home = scripticus_home()
    try:
        transaction = prepare_install(file, home)
    except (InstallError, ManifestError) as exc:
        console.print(f"[red]error:[/red] {escape(str(exc))}")
        raise typer.Exit(code=1) from exc

    try:
        if transaction.action == "already-installed":
            console.print(
                f"{transaction.package_id} {transaction.version} is already installed"
                " — nothing to do"
            )
            return

        _print_transaction(transaction)

        if mode is None:
            console.print()
            if not typer.confirm("Proceed?"):
                console.print("Aborted — nothing installed.")
                raise typer.Exit(code=1)
        elif mode == "no-conflicts" and transaction.conflicts:
            console.print(
                "\n[red]error:[/red] aborting: the transaction would overwrite existing"
                " command shims (nothing installed; use --force all to overwrite)"
            )
            raise typer.Exit(code=1)

        apply_install(transaction, home)

        console.print(f"\nInstalled [bold]{transaction.package_id}[/bold] {transaction.version}")
        if transaction.conflicts:
            console.print("Overwritten shims:")
            for conflict in transaction.conflicts:
                console.print(f"  {conflict.shim}  (was {conflict.owner})")
    finally:
        shutil.rmtree(transaction.staging, ignore_errors=True)


def _print_remote_transaction(plan: RemotePlan, skip_tools: bool, tools_config: Tools) -> None:
    console.print(
        f"Resolving [bold]{plan.result.packages[-1].namespace}/"
        f"{plan.result.packages[-1].name}[/bold] from {plan.remote.name}"
        f" ({plan.remote.url})"
    )

    installs = [a for a in plan.actions if a.action == "install"]
    changes = [a for a in plan.actions if a.action != "install"]
    if installs:
        console.print("\nNew packages:")
        for action in installs:
            resolved = action.resolved
            commands = ", ".join(sorted(resolved.commands)) or "—"
            console.print(
                f"  {resolved.namespace}/{resolved.name}  {resolved.version}"
                f"  (commands: {commands})"
            )
    if changes:
        console.print("\nVersion changes:")
        for action in changes:
            resolved = action.resolved
            if action.action == "reinstall":
                console.print(
                    f"  {resolved.namespace}/{resolved.name}  {resolved.version}  (reinstall)"
                )
            else:
                marker = "  (downgrade!)" if action.action == "downgrade" else ""
                console.print(
                    f"  {resolved.namespace}/{resolved.name}"
                    f"  {action.installed_version} -> {resolved.version}{marker}"
                )

    console.print()
    if skip_tools:
        console.print("System tools: skipped (--skip-tools)")
    else:
        _print_remote_tools(plan, tools_config)

    if plan.conflicts:
        console.print("\nShim conflicts:")
        for conflict in plan.conflicts:
            console.print(
                f"  {conflict.shim}  currently owned by {conflict.owner}"
                " — will be overwritten"
            )


def _print_remote_tools(plan: RemotePlan, tools_config: Tools) -> None:
    if plan.required_tools:
        missing = plan.missing_required
        if not missing:
            status = "[found]"
        elif tools_config.install is not None:
            status = f"[will install: {', '.join(missing)}]"
        else:
            status = f"[NOT FOUND: {', '.join(missing)} — no installer configured]"
        names = ", ".join(t.name for t in plan.required_tools)
        console.print(f"Required system tools: {names}        {status}")
    if plan.optional_tools:
        missing = [t.name for t in plan.optional_tools if not t.found]
        status = (
            f"[NOT FOUND: {', '.join(missing)} — some features degraded]"
            if missing
            else "[found]"
        )
        names = ", ".join(t.name for t in plan.optional_tools)
        console.print(f"Optional system tools: {names}        {status}")


def _install_remote(
    target: str, remote_name: Optional[str], skip_tools: bool, mode: Optional[str]
) -> None:
    home = scripticus_home()
    try:
        root, spec = parse_target(target)
        remotes = load_remotes(home)
        tools_config = load_tools(home)
        lock = read_lockfile(home)
        chosen, result = resolve_root(
            remotes, remote_name, root, spec, current_os(), installed_closure(lock)
        )
        token = resolve_token(chosen, home)
        plan = build_plan(chosen, token, result, lock)
    except (RemoteInstallError, ConfigError, CredentialsError, ManifestError) as exc:
        console.print(f"[red]error:[/red] {escape(str(exc))}")
        raise typer.Exit(code=1) from exc

    tool_work = (not skip_tools) and bool(plan.missing_required)
    if not plan.actions and not tool_work:
        console.print(f"{root} {plan.root_version} is already installed — nothing to do")
        return

    # Pre-flight refusal (D44): required tools missing and no installer means
    # the install cannot complete — fail before the prompt, not after.
    if not skip_tools and plan.missing_required and tools_config.install is None:
        listed = ", ".join(plan.missing_required)
        console.print(
            f"[red]error:[/red] missing required system tools: {listed}"
            " — configure [tools] install in config.toml, install them"
            " yourself, or re-run with --skip-tools"
        )
        raise typer.Exit(code=1)

    _print_remote_transaction(plan, skip_tools, tools_config)

    if mode is None:
        console.print()
        if not typer.confirm("Proceed?"):
            console.print("Aborted — nothing installed.")
            raise typer.Exit(code=1)
    elif mode == "no-conflicts" and plan.conflicts:
        console.print(
            "\n[red]error:[/red] aborting: the transaction would overwrite existing"
            " command shims (nothing installed; use --force all to overwrite)"
        )
        raise typer.Exit(code=1)

    try:
        staging_root, staged = stage_downloads(plan)
    except RemoteInstallError as exc:
        console.print(f"[red]error:[/red] {escape(str(exc))}")
        raise typer.Exit(code=1) from exc

    try:
        if not skip_tools:
            install_missing_required(plan.missing_required, tools_config)
        apply_remote(plan, staged, home)
    except ToolError as exc:
        console.print(f"[red]error:[/red] {escape(str(exc))}")
        raise typer.Exit(code=1) from exc
    finally:
        shutil.rmtree(staging_root, ignore_errors=True)

    console.print(f"\nInstalled from [bold]{chosen.name}[/bold]:")
    for action in plan.actions:
        resolved = action.resolved
        console.print(f"  {resolved.namespace}/{resolved.name} {resolved.version}")
    if plan.conflicts:
        console.print("Overwritten shims:")
        for conflict in plan.conflicts:
            console.print(f"  {conflict.shim}  (was {conflict.owner})")


@app.command()
def uninstall(
    package: str = typer.Argument(
        ...,
        help="Installed package to remove, as 'name' or 'namespace/name'.",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Remove without asking for confirmation.",
    ),
) -> None:
    """Uninstall a package, removing its files and command shims."""
    home = scripticus_home()
    lock = read_lockfile(home)

    try:
        entry = find_installed(package, lock)
    except UninstallError as exc:
        console.print(f"[red]error:[/red] {escape(str(exc))}")
        raise typer.Exit(code=1) from exc

    replacements = find_replacements(entry, lock, home)

    package_id = f"{entry['namespace']}/{entry['name']}"
    console.print(f"Uninstalling [bold]{package_id}[/bold] {entry['version']}")
    if entry.get("shims"):
        console.print(f"\nCommand shims to remove: {', '.join(entry['shims'])}")
    else:
        console.print(
            "\nNo shared command shims to remove (other packages own them all);"
            " its fully-qualified shims go too."
        )

    if not yes:
        console.print()
        if not typer.confirm("Proceed?"):
            console.print("Aborted — nothing removed.")
            raise typer.Exit(code=1)

    apply_uninstall(entry, lock, home)
    console.print(f"\nUninstalled [bold]{package_id}[/bold] {entry['version']}")

    for shim in sorted(replacements):
        candidates = replacements[shim]
        if yes:
            _report_replacements(shim, candidates)
        else:
            _prompt_replacement(shim, candidates, lock, home)


def _report_replacements(shim: str, candidates: "list[Candidate]") -> None:
    """Non-interactive: never re-point silently, just say what's possible."""
    if len(candidates) == 1:
        candidate = candidates[0]
        console.print(
            f"\n'{shim}' is also provided by {candidate.package_id} — run"
            f" 'scripticus use {candidate.package_id} {shim}' to restore it."
        )
    else:
        console.print(f"\nSeveral packages provide '{shim}':")
        for candidate in candidates:
            console.print(f"  {candidate.package_id}  {candidate.version}")
        console.print(
            "No replacement selected by default — use"
            f" 'scripticus use <namespace/name> {shim}' to select one."
        )


def _prompt_replacement(
    shim: str, candidates: "list[Candidate]", lock: dict, home: Path
) -> None:
    console.print(f"\n'{shim}' is also provided by other installed packages:")
    console.print("  0) No replacement")
    for number, candidate in enumerate(candidates, start=1):
        console.print(f"  {number}) {candidate.package_id}  {candidate.version}")

    while True:
        choice = typer.prompt(
            f"Select a replacement for '{shim}'", type=int, default=0
        )
        if 0 <= choice <= len(candidates):
            break
        console.print(f"Please choose a number between 0 and {len(candidates)}.")
    if choice == 0:
        console.print(f"'{shim}' left without a shim.")
        return
    candidate = candidates[choice - 1]
    install_replacement(candidate, shim, lock, home)
    console.print(f"'{shim}' now points at {candidate.package_id} {candidate.version}")


@app.command()
def login(
    remote: str = typer.Argument(
        ...,
        help="Name of the remote to log in to.",
    ),
    url: Optional[str] = typer.Argument(
        None,
        help="The remote's URL — required the first time, when the remote"
        " isn't in config.toml yet (this registers it too).",
    ),
) -> None:
    """Store a Gitea access token for a remote (registering it if new)."""
    home = scripticus_home()

    try:
        remotes = load_remotes(home)
        target, updated_remotes = prepare_login(remote, url, remotes)
        if updated_remotes is not None:
            # Fail before the token prompt if config can't be written.
            save_remotes(home, updated_remotes)
    except (ConfigError, LoginError) as exc:
        console.print(f"[red]error:[/red] {escape(str(exc))}")
        raise typer.Exit(code=1) from exc

    token = typer.prompt("Token", hide_input=True)

    # Verify against the remote before storing (D41): a bad or mistyped
    # token fails here, clearly, rather than at first publish. Nothing is
    # written to credentials.toml unless the token authenticates.
    try:
        identity = verify_token(target, token)
    except WhoAmIError as exc:
        console.print(f"[red]error:[/red] {escape(str(exc))}")
        raise typer.Exit(code=1) from exc

    set_token(home, target.url, token)
    console.print(
        f"Logged in to [bold]{target.name}[/bold] ({target.url})"
        f" as [bold]{escape(identity.username)}[/bold]"
    )


@app.command()
def publish(
    path_prefix: Path = typer.Argument(
        ...,
        help="Path whose last component is the archives' <name>-<version>"
        " prefix, e.g. builds/my-cool-script-0.1.2.",
    ),
    remote: Optional[str] = typer.Option(
        None,
        "--remote",
        help="Named remote to publish to (default: the first in config.toml).",
    ),
) -> None:
    """Publish a version's pre-built archives to a remote, as one batch."""
    home = scripticus_home()

    try:
        remotes = load_remotes(home)
        target = resolve_remote(remote, remotes)
        token = resolve_token(target, home)
        archives = matching_archives(path_prefix)
        result = publish_archives(target, token, archives)
    except (ConfigError, CredentialsError, PublishError) as exc:
        console.print(f"[red]error:[/red] {escape(str(exc))}")
        raise typer.Exit(code=1) from exc

    console.print(f"Published [bold]{result.name}[/bold] {result.version}:")
    for artifact in result.artifacts:
        console.print(f"  {artifact.filename}")


@app.command()
def use(
    package: str = typer.Argument(
        ...,
        help="Installed package to point the shim at, as 'name' or 'namespace/name'.",
    ),
    shim: str = typer.Argument(
        ...,
        help="The convenience shim to re-point: a bare command ('clash') or"
        " a namespaced one ('acme.clash'). Fully-qualified shims are fixed.",
    ),
) -> None:
    """Point a convenience command shim at a specific installed package."""
    home = scripticus_home()
    lock = read_lockfile(home)

    try:
        candidate, owner = prepare_use(package, shim, lock, home)
    except UseError as exc:
        console.print(f"[red]error:[/red] {escape(str(exc))}")
        raise typer.Exit(code=1) from exc

    if (
        owner is not None
        and (owner["namespace"], owner["name"]) == (candidate.namespace, candidate.name)
    ):
        console.print(
            f"'{shim}' already points at {candidate.package_id} {candidate.version}"
            " — nothing to do"
        )
        return

    install_replacement(candidate, shim, lock, home)
    was = (
        f" (was {owner['namespace']}/{owner['name']} {owner['version']})"
        if owner is not None
        else " (previously had no shim)"
    )
    console.print(
        f"'{shim}' now points at [bold]{candidate.package_id}[/bold]"
        f" {candidate.version}{was}"
    )
