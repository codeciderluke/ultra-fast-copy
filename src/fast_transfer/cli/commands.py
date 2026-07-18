"""Typer command implementations."""

from __future__ import annotations

import contextlib
import signal
import sys
import time
from dataclasses import replace
from enum import StrEnum
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from .. import APP_NAME, __version__
from ..config.defaults import config_path, preset_options
from ..config.settings import load_settings, write_default_config
from ..core.checkpoint import list_checkpoints
from ..core.control import TransferControl
from ..core.engine import TransferEngine, resume_job
from ..core.errors import TransferError
from ..core.models import (
    ConflictPolicy,
    JobStatus,
    OperationType,
    ScanMode,
    SpeedPreset,
    SymlinkPolicy,
    TransferJob,
    TransferOptions,
    TransferResult,
    VerifyMode,
)
from ..core.scanner import Scanner
from ..utils.formatting import format_count, format_duration, format_size, parse_size
from ..utils.logging import configure_logging, default_log_file
from ..utils.paths import display_path, normalize
from ..utils.system import describe_pair, recommend_workers
from .renderer import ACCENT, BAD, MUTED, OK, WARN, JsonRenderer, LiveRenderer, print_summary

console = Console()

# Exit codes, so scripts can branch on the outcome.
EXIT_OK = 0
EXIT_PARTIAL = 1  # completed but some files failed
EXIT_FAILED = 2  # the job itself failed
EXIT_CANCELLED = 3
EXIT_USAGE = 4


class Options:
    """Reusable Typer option definitions shared by `copy` and `move`.

    These go inside `Annotated[...]`, so they carry declarations only -- the
    default value lives on the function signature. Passing a default here would
    be read as another declaration.
    """

    workers = typer.Option("--workers", "-w", help="Worker threads. Omit to auto tune.")
    buffer_size = typer.Option("--buffer-size", help="Copy buffer, e.g. 4MiB.")
    verify = typer.Option("--verify", help="none | size | mtime_size | xxhash | sha256")
    conflict = typer.Option("--conflict", help="What to do when the destination exists.")
    retry = typer.Option("--retry", help="Retries per file for transient errors.")
    prescan = typer.Option("--prescan/--streaming", help="Count everything first, or start immediately.")
    resume = typer.Option("--resume/--no-resume", help="Write a checkpoint so the job can resume.")
    include = typer.Option("--include", help="Only transfer paths matching this glob. Repeatable.")
    exclude = typer.Option("--exclude", help="Skip paths matching this glob. Repeatable.")
    follow_symlinks = typer.Option("--follow-symlinks", help="Walk into symlinks and junctions.")
    preserve_times = typer.Option("--preserve-times/--no-preserve-times", help="Copy timestamps.")
    preserve_permissions = typer.Option("--preserve-permissions", help="Copy permission bits.")
    dry_run = typer.Option("--dry-run", help="Report what would happen without writing.")
    quiet = typer.Option("--quiet", "-q", help="No progress display.")
    verbose = typer.Option("--verbose", "-v", help="Log every file.")
    log_file = typer.Option("--log-file", help="Write the log here.")
    json_output = typer.Option("--json-output", help="Emit JSON events on stdout.")
    bandwidth = typer.Option("--bandwidth-limit", help="Throttle throughput, e.g. 10MiB.")
    delete_partial = typer.Option(
        "--delete-partial-on-failure/--keep-partial-on-failure",
        help="Remove incomplete files when a transfer fails.",
    )
    preset = typer.Option("--preset", help="fast | balanced | safe")
    hidden = typer.Option("--hidden/--no-hidden", help="Include hidden files.")
    system_files = typer.Option("--system-files", help="Include system files.")


def build_options(
    operation: OperationType,
    *,
    workers: int | None = None,
    buffer_size: str | None = None,
    verify: str | None = None,
    conflict: str | None = None,
    retry: int | None = None,
    prescan: bool | None = None,
    resume: bool = True,
    include: list[str] | None = None,
    exclude: list[str] | None = None,
    follow_symlinks: bool = False,
    preserve_times: bool | None = None,
    preserve_permissions: bool = False,
    dry_run: bool = False,
    bandwidth: str | None = None,
    delete_partial: bool = True,
    preset: str | None = None,
    hidden: bool | None = None,
    system_files: bool = False,
    interactive: bool = True,
) -> TransferOptions:
    """Merge config file, preset, and CLI flags. CLI wins."""
    settings = load_settings()
    for error in settings.load_errors:
        console.print(f"[{BAD}]Config: {error}[/]")

    options = settings.transfer
    if preset is not None:
        options = preset_options(_enum(SpeedPreset, preset, "--preset"), options)

    options = replace(options, operation=operation)

    if workers is not None:
        options.workers = workers if workers > 0 else None
    if buffer_size is not None:
        options.buffer_size = _size(buffer_size, "--buffer-size")
    if verify is not None:
        options.verify = _enum(VerifyMode, verify, "--verify")
    if conflict is not None:
        options.conflict = _enum(ConflictPolicy, conflict.replace("-", "_"), "--conflict")
    if retry is not None:
        options.retry_count = retry
    if prescan is not None:
        options.scan_mode = ScanMode.PRESCAN if prescan else ScanMode.STREAMING
    if preserve_times is not None:
        options.preserve_times = preserve_times
    if hidden is not None:
        options.include_hidden = hidden
    if bandwidth is not None:
        options.bandwidth_limit = _size(bandwidth, "--bandwidth-limit") or None

    options.use_checkpoint = resume
    options.include_patterns = tuple(include or ())
    options.exclude_patterns = tuple(exclude or ())
    options.symlink_policy = SymlinkPolicy.FOLLOW if follow_symlinks else options.symlink_policy
    options.preserve_permissions = preserve_permissions or options.preserve_permissions
    options.include_system = system_files or options.include_system
    options.dry_run = dry_run
    options.delete_partial_on_failure = delete_partial

    # `ask` cannot work without a terminal; degrade to the safe default.
    if options.conflict is ConflictPolicy.ASK and not interactive:
        console.print(
            f"[{MUTED}]Not an interactive terminal; --conflict ask falls back to skip.[/]"
        )
        options.conflict = ConflictPolicy.SKIP

    return options


def _enum[EnumT: StrEnum](enum_type: type[EnumT], raw: str, flag: str) -> EnumT:
    """Parse `raw` into `enum_type`, or exit with the list of valid values."""
    try:
        return enum_type(raw)
    except ValueError:
        allowed = ", ".join(member.value for member in enum_type)
        console.print(f"[{BAD}]{flag}: '{raw}' is not valid. Expected one of: {allowed}[/]")
        raise typer.Exit(EXIT_USAGE) from None


def _size(raw: str, flag: str) -> int:
    try:
        return parse_size(raw)
    except ValueError as exc:
        console.print(f"[{BAD}]{flag}: {exc}[/]")
        raise typer.Exit(EXIT_USAGE) from None


def _ask_conflict(source: Path, destination: Path) -> ConflictPolicy:
    """Interactive `--conflict ask` prompt."""
    console.print(f"\n[{ACCENT}]Conflict:[/] {display_path(destination)} already exists.")
    answer = typer.prompt(
        "  [s]kip / [o]verwrite / [r]ename / [n]ewer only",
        default="s",
        show_default=True,
    ).strip().lower()
    return {
        "s": ConflictPolicy.SKIP,
        "o": ConflictPolicy.OVERWRITE,
        "r": ConflictPolicy.RENAME,
        "n": ConflictPolicy.OVERWRITE_IF_NEWER,
    }.get(answer[:1], ConflictPolicy.SKIP)


def run_transfer(
    sources: list[Path],
    destination: Path,
    options: TransferOptions,
    *,
    quiet: bool,
    verbose: bool,
    json_output: bool,
    log_file: Path | None,
) -> int:
    """Wire an engine to a renderer, handle Ctrl+C, and return an exit code."""
    configure_logging(log_file=log_file, console=False)

    job = TransferJob(
        sources=tuple(normalize(s) for s in sources),
        destination=normalize(destination),
        options=options,
    )
    control = TransferControl()
    engine = TransferEngine(
        job,
        control=control,
        ask_callback=_ask_conflict if options.conflict is ConflictPolicy.ASK else None,
    )

    _install_sigint(control)

    renderer: JsonRenderer | LiveRenderer = (
        JsonRenderer() if json_output else LiveRenderer(console, quiet=quiet, verbose=verbose)
    )
    with renderer:
        engine.emitter.subscribe(renderer.handle)
        result = engine.run()

    if not json_output and not quiet:
        print_summary(console, result)
    elif not json_output and quiet and result.failed_files:
        console.print(f"[{BAD}]{result.failed_files:,} file(s) failed. See {default_log_file()}[/]")

    return _exit_code(result)


def _install_sigint(control: TransferControl) -> None:
    """First Ctrl+C asks the engine to stop; a second one kills the process."""
    hits = {"count": 0}

    def handler(_signum: int, _frame: object) -> None:
        hits["count"] += 1
        if hits["count"] == 1:
            console.print(f"\n[{MUTED}]Cancelling... press Ctrl+C again to force quit.[/]")
            control.cancel()
        else:
            console.print(f"\n[{BAD}]Forced exit. Partial files may remain.[/]")
            sys.exit(EXIT_CANCELLED)

    # ValueError: not on the main thread, so the caller handles cancellation.
    with contextlib.suppress(ValueError):
        signal.signal(signal.SIGINT, handler)


def _exit_code(result: TransferResult) -> int:
    match result.status:
        case JobStatus.COMPLETED:
            return EXIT_OK
        case JobStatus.COMPLETED_WITH_ERRORS:
            return EXIT_PARTIAL
        case JobStatus.CANCELLED:
            return EXIT_CANCELLED
        case _:
            return EXIT_FAILED


# -- command bodies --------------------------------------------------------


def cmd_jobs() -> int:
    """List resumable jobs."""
    jobs = list_checkpoints()
    if not jobs:
        console.print(f"[{MUTED}]No resumable jobs.[/]")
        return EXIT_OK

    table = Table(title="Resumable jobs", border_style=ACCENT, header_style=ACCENT)
    table.add_column("Job ID")
    table.add_column("Op")
    table.add_column("Source", overflow="fold")
    table.add_column("Destination", overflow="fold")
    table.add_column("Progress", justify="right")
    table.add_column("Updated")
    for meta in jobs:
        progress = (
            f"{meta.completed_files:,} / {meta.total_files:,}" if meta.total_files else f"{meta.completed_files:,}"
        )
        table.add_row(
            meta.job_id,
            meta.operation.value,
            display_path(meta.sources[0] if meta.sources else "-"),
            display_path(meta.destination),
            progress,
            time.strftime("%Y-%m-%d %H:%M", time.localtime(meta.updated_at)),
        )
    console.print(table)
    console.print(f"[{MUTED}]Resume with: ufCopy resume <JOB_ID>[/]")
    return EXIT_OK


def cmd_resume(job_id: str, quiet: bool, verbose: bool, json_output: bool) -> int:
    configure_logging()
    control = TransferControl()
    _install_sigint(control)
    renderer: JsonRenderer | LiveRenderer = (
        JsonRenderer() if json_output else LiveRenderer(console, quiet=quiet, verbose=verbose)
    )
    try:
        with renderer:
            from ..core.events import EventEmitter

            emitter = EventEmitter()
            emitter.subscribe(renderer.handle)
            result = resume_job(job_id, emitter=emitter, control=control)
    except TransferError as exc:
        console.print(f"[{BAD}]{exc}[/]")
        return EXIT_FAILED

    if not json_output and not quiet:
        print_summary(console, result)
    return _exit_code(result)


def cmd_config_show() -> int:
    path = config_path()
    settings = load_settings()
    table = Table(title=f"{APP_NAME} configuration", border_style=ACCENT, header_style=ACCENT)
    table.add_column("Setting", style=MUTED)
    table.add_column("Value")
    transfer = settings.transfer
    table.add_row("config file", str(path) + ("" if path.exists() else "  (not created yet)"))
    table.add_row("workers", str(transfer.workers or "auto"))
    table.add_row("buffer_size", format_size(transfer.buffer_size))
    table.add_row("verify", transfer.verify.value)
    table.add_row("conflict", transfer.conflict.value)
    table.add_row("retry_count", str(transfer.retry_count))
    table.add_row("scan_mode", transfer.scan_mode.value)
    table.add_row("checkpoint", str(transfer.use_checkpoint))
    table.add_row("preserve_times", str(transfer.preserve_times))
    table.add_row("bandwidth_limit", format_size(transfer.bandwidth_limit) if transfer.bandwidth_limit else "unlimited")
    table.add_row("log file", str(default_log_file()))
    console.print(table)
    for error in settings.load_errors:
        console.print(f"[{BAD}]{error}[/]")
    return EXIT_OK


def cmd_config_init(force: bool) -> int:
    path = config_path()
    if path.exists() and not force:
        console.print(f"[{MUTED}]{path} already exists. Use --force to overwrite.[/]")
        return EXIT_USAGE
    written = write_default_config()
    console.print(f"[{OK}]Wrote {written}[/]")
    return EXIT_OK


def cmd_benchmark(source: Path, destination: Path, workers: list[int] | None) -> int:
    """Time the same copy at several worker counts to find this machine's sweet spot."""
    configure_logging()
    source = normalize(source)
    destination = normalize(destination)

    scanner = Scanner()
    stats = scanner.measure([source])
    if stats.total_files == 0:
        console.print(f"[{BAD}]No files found under {display_path(source)}[/]")
        return EXIT_USAGE

    console.print(
        f"[{ACCENT}]{format_count(stats.total_files)} files, {format_size(stats.total_bytes)} "
        f"| {describe_pair(source, destination)}[/]"
    )
    auto = recommend_workers(source, destination, stats.total_bytes // max(1, stats.total_files))
    candidates = workers or sorted({1, 4, auto, 16})
    console.print(f"[{MUTED}]Recommended worker count for this pair: {auto}[/]\n")

    table = Table(title="Benchmark", border_style=ACCENT, header_style=ACCENT)
    table.add_column("Workers", justify="right")
    table.add_column("Elapsed", justify="right")
    table.add_column("Files/s", justify="right")
    table.add_column("Throughput", justify="right")
    table.add_column("Failed", justify="right")

    for count in candidates:
        target = destination / f"benchmark_w{count}"
        options = TransferOptions(
            operation=OperationType.COPY,
            workers=count,
            verify=VerifyMode.NONE,
            conflict=ConflictPolicy.OVERWRITE,
            use_checkpoint=False,
            scan_mode=ScanMode.PRESCAN,
        )
        job = TransferJob(sources=(source,), destination=target, options=options)
        with console.status(f"Running with {count} worker(s)..."):
            result = TransferEngine(job).run()
        files_per_second = result.completed_files / result.elapsed_seconds if result.elapsed_seconds else 0
        table.add_row(
            str(count),
            format_duration(result.elapsed_seconds),
            f"{files_per_second:,.0f}",
            f"{format_size(result.average_speed_bps)}/s",
            str(result.failed_files),
        )
        _remove_tree(target)

    console.print(table)
    console.print(f"[{MUTED}]Benchmark copies were removed. Compare against Explorer and robocopy for context.[/]")
    return EXIT_OK


def _remove_tree(path: Path) -> None:
    import shutil

    from ..utils.paths import extended_path

    with contextlib.suppress(OSError):
        shutil.rmtree(extended_path(path), ignore_errors=True)


def cmd_version() -> int:
    console.print(f"[{ACCENT}]{APP_NAME}[/] {__version__}")
    return EXIT_OK


def cmd_shell_install() -> int:
    """Add the Explorer right-click entry for the current user."""
    from ..integration import context_menu

    try:
        result = context_menu.install()
    except OSError as exc:
        console.print(f"[{BAD}]{exc}[/]")
        return EXIT_FAILED

    console.print(f"[{OK}]Added '{context_menu.MENU_TEXT}' to the Explorer context menu.[/]")
    console.print(f"[{MUTED}]Command: {result.command}[/]")
    console.print(
        f"[{MUTED}]On Windows 11 it appears under 'Show more options' (Shift+F10).[/]"
    )
    return EXIT_OK


def cmd_shell_uninstall() -> int:
    from ..integration import context_menu

    try:
        context_menu.uninstall()
    except OSError as exc:
        console.print(f"[{BAD}]{exc}[/]")
        return EXIT_FAILED
    console.print(f"[{OK}]Removed the Explorer context menu entry.[/]")
    return EXIT_OK


def cmd_shell_status() -> int:
    from ..integration import context_menu

    try:
        result = context_menu.status()
    except OSError as exc:
        console.print(f"[{BAD}]{exc}[/]")
        return EXIT_FAILED

    if not result.installed:
        console.print(f"[{MUTED}]Not registered. Add it with: ufCopy shell install[/]")
        return EXIT_OK

    table = Table(title="Explorer context menu", border_style=ACCENT, header_style=ACCENT)
    table.add_column("Setting", style=MUTED)
    table.add_column("Value", overflow="fold")
    table.add_row("label", context_menu.MENU_TEXT)
    table.add_row("command", result.command)
    table.add_row("icon", result.icon or "(default)")
    for location in result.locations:
        table.add_row("registry", f"HKCU\\{location}")
    console.print(table)

    if context_menu.stale(result):
        console.print(
            f"[{WARN}]The registered command does not match this install. "
            f"Re-run: ufCopy shell install[/]"
        )
    return EXIT_OK
