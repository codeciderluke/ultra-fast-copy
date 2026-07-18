"""`ufCopy` command line entry point."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated

import typer

from .. import APP_NAME, __version__
from ..core.models import OperationType
from . import commands
from .commands import EXIT_OK, Options, build_options, console, run_transfer

app = typer.Typer(
    name="ufCopy",
    help=f"{APP_NAME} - fast file copy and move for Windows.",
    add_completion=False,
    no_args_is_help=True,
    rich_markup_mode="rich",
)
config_app = typer.Typer(help="Inspect and create the configuration file.", no_args_is_help=True)
app.add_typer(config_app, name="config")

shell_app = typer.Typer(help="Explorer right-click menu integration.", no_args_is_help=True)
app.add_typer(shell_app, name="shell")


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"{APP_NAME} {__version__}")
        raise typer.Exit(EXIT_OK)


@app.callback()
def main_callback(
    version: Annotated[
        bool, typer.Option("--version", callback=_version_callback, is_eager=True, help="Show the version.")
    ] = False,
) -> None:
    """Ultra Fast Copy."""


@app.command()
def copy(
    source: Annotated[list[Path], typer.Argument(help="Source file or folder. Repeatable.")],
    destination: Annotated[Path, typer.Argument(help="Destination folder.")],
    workers: Annotated[int | None, Options.workers] = None,
    buffer_size: Annotated[str | None, Options.buffer_size] = None,
    verify: Annotated[str | None, Options.verify] = None,
    conflict: Annotated[str | None, Options.conflict] = None,
    retry: Annotated[int | None, Options.retry] = None,
    prescan: Annotated[bool | None, Options.prescan] = None,
    resume: Annotated[bool, Options.resume] = True,
    include: Annotated[list[str] | None, Options.include] = None,
    exclude: Annotated[list[str] | None, Options.exclude] = None,
    follow_symlinks: Annotated[bool, Options.follow_symlinks] = False,
    preserve_times: Annotated[bool | None, Options.preserve_times] = None,
    preserve_permissions: Annotated[bool, Options.preserve_permissions] = False,
    dry_run: Annotated[bool, Options.dry_run] = False,
    quiet: Annotated[bool, Options.quiet] = False,
    verbose: Annotated[bool, Options.verbose] = False,
    log_file: Annotated[Path | None, Options.log_file] = None,
    json_output: Annotated[bool, Options.json_output] = False,
    bandwidth_limit: Annotated[str | None, Options.bandwidth] = None,
    delete_partial_on_failure: Annotated[bool, Options.delete_partial] = True,
    preset: Annotated[str | None, Options.preset] = None,
    hidden: Annotated[bool | None, Options.hidden] = None,
    system_files: Annotated[bool, Options.system_files] = False,
) -> None:
    """Copy files or folders to DESTINATION."""
    options = build_options(
        OperationType.COPY,
        workers=workers,
        buffer_size=buffer_size,
        verify=verify,
        conflict=conflict,
        retry=retry,
        prescan=prescan,
        resume=resume,
        include=include,
        exclude=exclude,
        follow_symlinks=follow_symlinks,
        preserve_times=preserve_times,
        preserve_permissions=preserve_permissions,
        dry_run=dry_run,
        bandwidth=bandwidth_limit,
        delete_partial=delete_partial_on_failure,
        preset=preset,
        hidden=hidden,
        system_files=system_files,
        interactive=sys.stdin.isatty(),
    )
    raise typer.Exit(
        run_transfer(
            source,
            destination,
            options,
            quiet=quiet,
            verbose=verbose,
            json_output=json_output,
            log_file=log_file,
        )
    )


@app.command()
def move(
    source: Annotated[list[Path], typer.Argument(help="Source file or folder. Repeatable.")],
    destination: Annotated[Path, typer.Argument(help="Destination folder.")],
    workers: Annotated[int | None, Options.workers] = None,
    buffer_size: Annotated[str | None, Options.buffer_size] = None,
    verify: Annotated[str | None, Options.verify] = None,
    conflict: Annotated[str | None, Options.conflict] = None,
    retry: Annotated[int | None, Options.retry] = None,
    prescan: Annotated[bool | None, Options.prescan] = None,
    resume: Annotated[bool, Options.resume] = True,
    include: Annotated[list[str] | None, Options.include] = None,
    exclude: Annotated[list[str] | None, Options.exclude] = None,
    follow_symlinks: Annotated[bool, Options.follow_symlinks] = False,
    preserve_times: Annotated[bool | None, Options.preserve_times] = None,
    preserve_permissions: Annotated[bool, Options.preserve_permissions] = False,
    dry_run: Annotated[bool, Options.dry_run] = False,
    quiet: Annotated[bool, Options.quiet] = False,
    verbose: Annotated[bool, Options.verbose] = False,
    log_file: Annotated[Path | None, Options.log_file] = None,
    json_output: Annotated[bool, Options.json_output] = False,
    bandwidth_limit: Annotated[str | None, Options.bandwidth] = None,
    delete_partial_on_failure: Annotated[bool, Options.delete_partial] = True,
    preset: Annotated[str | None, Options.preset] = None,
    hidden: Annotated[bool | None, Options.hidden] = None,
    system_files: Annotated[bool, Options.system_files] = False,
) -> None:
    """Move files or folders to DESTINATION.

    Same volume moves are renames. Cross volume moves copy, verify, then delete
    the original -- an unverified copy never causes a deletion.
    """
    options = build_options(
        OperationType.MOVE,
        workers=workers,
        buffer_size=buffer_size,
        verify=verify,
        conflict=conflict,
        retry=retry,
        prescan=prescan,
        resume=resume,
        include=include,
        exclude=exclude,
        follow_symlinks=follow_symlinks,
        preserve_times=preserve_times,
        preserve_permissions=preserve_permissions,
        dry_run=dry_run,
        bandwidth=bandwidth_limit,
        delete_partial=delete_partial_on_failure,
        preset=preset,
        hidden=hidden,
        system_files=system_files,
        interactive=sys.stdin.isatty(),
    )
    raise typer.Exit(
        run_transfer(
            source,
            destination,
            options,
            quiet=quiet,
            verbose=verbose,
            json_output=json_output,
            log_file=log_file,
        )
    )


@app.command()
def resume(
    job_id: Annotated[str, typer.Argument(help="Job ID from `ufCopy jobs`.")],
    quiet: Annotated[bool, Options.quiet] = False,
    verbose: Annotated[bool, Options.verbose] = False,
    json_output: Annotated[bool, Options.json_output] = False,
) -> None:
    """Continue an interrupted job, skipping files already verified as complete."""
    raise typer.Exit(commands.cmd_resume(job_id, quiet, verbose, json_output))


@app.command()
def jobs() -> None:
    """List jobs that can be resumed."""
    raise typer.Exit(commands.cmd_jobs())


@app.command()
def benchmark(
    source: Annotated[Path, typer.Argument(help="Folder to copy from.")],
    destination: Annotated[Path, typer.Argument(help="Folder to copy into.")],
    workers: Annotated[
        list[int] | None, typer.Option("--workers", "-w", help="Worker counts to try. Repeatable.")
    ] = None,
) -> None:
    """Time the same copy at several worker counts. Benchmark copies are deleted afterwards."""
    raise typer.Exit(commands.cmd_benchmark(source, destination, workers))


@config_app.command("show")
def config_show() -> None:
    """Print the effective configuration."""
    raise typer.Exit(commands.cmd_config_show())


@config_app.command("init")
def config_init(
    force: Annotated[bool, typer.Option("--force", help="Overwrite an existing config file.")] = False,
) -> None:
    """Write a commented default config file."""
    raise typer.Exit(commands.cmd_config_init(force))


@shell_app.command("install")
def shell_install() -> None:
    """Add 'Open with Ultra Fast Copy' to the Explorer right-click menu."""
    raise typer.Exit(commands.cmd_shell_install())


@shell_app.command("uninstall")
def shell_uninstall() -> None:
    """Remove the Explorer right-click menu entry."""
    raise typer.Exit(commands.cmd_shell_uninstall())


@shell_app.command("status")
def shell_status() -> None:
    """Show whether the Explorer menu entry is registered."""
    raise typer.Exit(commands.cmd_shell_status())


@app.command()
def gui(
    source: Annotated[
        Path | None, typer.Argument(help="Path to open in the source pane.")
    ] = None,
) -> None:
    """Launch the graphical version."""
    from ..gui.app import main as gui_main

    raise typer.Exit(gui_main(["--source", str(source)] if source else []))


def main() -> int:
    """Console script entry point."""
    try:
        app()
        return EXIT_OK
    except KeyboardInterrupt:
        console.print("\nInterrupted.")
        return 3


if __name__ == "__main__":
    sys.exit(main())
