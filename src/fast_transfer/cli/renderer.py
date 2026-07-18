"""Console rendering: a live Rich dashboard, or newline-delimited JSON events."""

from __future__ import annotations

import json
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from rich.console import Console, Group, RenderableType
from rich.live import Live
from rich.panel import Panel
from rich.progress import BarColumn, Progress, TaskID, TextColumn
from rich.table import Table
from rich.text import Text

from ..core.events import (
    Event,
    FileEvent,
    JobStateEvent,
    LogEvent,
    ProgressEvent,
    ScanProgressEvent,
)
from ..core.models import JobStatus, TransferResult
from ..utils.formatting import (
    format_count,
    format_duration,
    format_size,
    format_speed,
    truncate_middle,
)
from ..utils.paths import display_path

# Dark palette shared with the GUI so both front-ends look like one product.
ACCENT = "#4cc2ff"
MUTED = "#8b93a7"
OK = "#3ddc84"
WARN = "#ffb454"
BAD = "#ff5f6b"


@dataclass(slots=True)
class RenderState:
    """Latest values pulled from events, read by the render loop."""

    scanning: bool = True
    scanned_files: int = 0
    scanned_bytes: int = 0
    completed_files: int = 0
    total_files: int | None = None
    completed_bytes: int = 0
    total_bytes: int | None = None
    current_file: str = ""
    speed: float = 0.0
    average: float = 0.0
    eta: float | None = None
    failed: int = 0
    skipped: int = 0
    retries: int = 0
    status: JobStatus = JobStatus.PENDING


class LiveRenderer:
    """Renders engine events as a live dashboard.

    Rich redraws on its own schedule, so this only mutates state -- it never
    draws from a worker thread.
    """

    def __init__(self, console: Console | None = None, *, quiet: bool = False, verbose: bool = False) -> None:
        self.console = console or Console()
        self._quiet = quiet
        self._verbose = verbose
        self._state = RenderState()
        self._lock = threading.Lock()
        self._log_lines: list[Text] = []
        self._progress = Progress(
            TextColumn("[bold]{task.description}"),
            BarColumn(bar_width=None, complete_style=ACCENT, finished_style=OK),
            TextColumn("{task.percentage:>5.1f}%"),
            expand=True,
        )
        self._task: TaskID | None = None
        self._live: Live | None = None

    def __enter__(self) -> LiveRenderer:
        if not self._quiet:
            self._task = self._progress.add_task("Preparing", total=None)
            self._live = Live(
                self._render(), console=self.console, refresh_per_second=8, transient=False
            )
            self._live.start(refresh=True)
        return self

    def __exit__(self, *_exc_info: object) -> None:
        if self._live is not None:
            self._live.update(self._render())
            self._live.stop()
            self._live = None

    def handle(self, event: Event) -> None:
        """Engine callback. Cheap and non-blocking."""
        with self._lock:
            self._apply(event)
        if self._live is not None:
            self._live.update(self._render())

    def _apply(self, event: Event) -> None:
        state = self._state
        match event:
            case ScanProgressEvent():
                state.scanning = not event.done
                state.scanned_files = event.scanned_files
                state.scanned_bytes = event.scanned_bytes
                if event.done:
                    state.total_files = event.scanned_files
                    state.total_bytes = event.scanned_bytes
            case ProgressEvent():
                state.scanning = False
                state.completed_files = event.completed_files
                state.total_files = event.total_files
                state.completed_bytes = event.completed_bytes
                state.total_bytes = event.total_bytes
                state.current_file = display_path(event.current_file or "")
                state.speed = event.current_speed_bps
                state.average = event.average_speed_bps
                state.eta = event.eta_seconds
                state.failed = event.failed_files
                state.skipped = event.skipped_files
            case FileEvent():
                if event.outcome == "failed":
                    self._push_log("ERROR", f"{display_path(event.source)}: {event.message}")
                elif self._verbose:
                    self._push_log("INFO", f"{event.outcome}: {display_path(event.source)}")
            case JobStateEvent():
                state.status = event.status
            case LogEvent():
                if self._verbose or event.level in ("WARNING", "ERROR"):
                    self._push_log(event.level, event.message)

    def _push_log(self, level: str, message: str) -> None:
        color = {"ERROR": BAD, "WARNING": WARN}.get(level, MUTED)
        self._log_lines.append(Text(f"{level:<7} {message}", style=color))
        del self._log_lines[:-8]  # keep the tail only

    def _render(self) -> Group:
        state = self._state
        if self._task is not None:
            total = state.total_bytes or None
            self._progress.update(
                self._task,
                description=_describe(state),
                total=total,
                completed=min(state.completed_bytes, total) if total else state.completed_bytes,
            )

        table = Table.grid(padding=(0, 2))
        table.add_column(style=MUTED, justify="right")
        table.add_column(style="bold")
        table.add_column(style=MUTED, justify="right")
        table.add_column(style="bold")

        files = (
            f"{format_count(state.completed_files)} / {format_count(state.total_files)}"
            if state.total_files
            else format_count(state.completed_files)
        )
        size = (
            f"{format_size(state.completed_bytes)} / {format_size(state.total_bytes)}"
            if state.total_bytes
            else format_size(state.completed_bytes)
        )
        table.add_row("Files", files, "Speed", format_speed(state.speed))
        table.add_row("Size", size, "Average", format_speed(state.average))
        table.add_row(
            "Remaining",
            format_duration(state.eta),
            "Failed",
            Text(format_count(state.failed), style=BAD if state.failed else OK),
        )
        table.add_row("Skipped", format_count(state.skipped), "Status", state.status.value)

        current = Text(
            truncate_middle(state.current_file or "-", 78), style=ACCENT, overflow="ellipsis"
        )
        body: list[RenderableType] = [self._progress, "", table, "", current]
        if self._log_lines:
            body.extend(["", *self._log_lines])
        return Group(Panel(Group(*body), title="Ultra Fast Copy", border_style=ACCENT))


def _describe(state: RenderState) -> str:
    if state.scanning:
        return f"Scanning ({format_count(state.scanned_files)} files)"
    return state.status.value.replace("_", " ").title()


class JsonRenderer:
    """Emits one JSON object per line on stdout for automation."""

    def __init__(self, stream: TextIO | None = None) -> None:
        self._stream: TextIO = stream if stream is not None else sys.stdout
        self._lock = threading.Lock()

    def __enter__(self) -> JsonRenderer:
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self._stream.flush()

    def handle(self, event: Event) -> None:
        payload = self._to_dict(event)
        if payload is None:
            return
        with self._lock:
            self._stream.write(json.dumps(payload, ensure_ascii=False) + "\n")
            self._stream.flush()

    @staticmethod
    def _to_dict(event: Event) -> dict[str, object] | None:
        match event:
            case ScanProgressEvent():
                return {
                    "type": "scan",
                    "job_id": event.job_id,
                    "scanned_files": event.scanned_files,
                    "scanned_bytes": event.scanned_bytes,
                    "done": event.done,
                }
            case ProgressEvent():
                return {
                    "type": "progress",
                    "job_id": event.job_id,
                    "completed_files": event.completed_files,
                    "total_files": event.total_files,
                    "completed_bytes": event.completed_bytes,
                    "total_bytes": event.total_bytes,
                    "current_file": display_path(event.current_file or ""),
                    "speed_bps": round(event.current_speed_bps, 2),
                    "average_bps": round(event.average_speed_bps, 2),
                    "eta_seconds": event.eta_seconds,
                    "failed_files": event.failed_files,
                    "skipped_files": event.skipped_files,
                }
            case FileEvent():
                return {
                    "type": "file",
                    "job_id": event.job_id,
                    "source": display_path(event.source),
                    "destination": display_path(event.destination),
                    "size": event.size,
                    "outcome": event.outcome,
                    "error_code": event.error_code,
                    "message": event.message,
                }
            case JobStateEvent():
                payload: dict[str, object] = {
                    "type": "state",
                    "job_id": event.job_id,
                    "status": event.status.value,
                }
                if event.result is not None:
                    payload["result"] = event.result.as_dict()
                return payload
            case LogEvent():
                return {
                    "type": "log",
                    "job_id": event.job_id,
                    "level": event.level,
                    "message": event.message,
                }
            case _:
                return None


def print_summary(console: Console, result: TransferResult) -> None:
    """Final table shown after a run."""
    style = {
        JobStatus.COMPLETED: OK,
        JobStatus.COMPLETED_WITH_ERRORS: WARN,
        JobStatus.CANCELLED: WARN,
        JobStatus.FAILED: BAD,
    }.get(result.status, MUTED)

    table = Table.grid(padding=(0, 2))
    table.add_column(style=MUTED, justify="right")
    table.add_column()
    table.add_row("Job", result.job_id)
    table.add_row("Operation", result.operation.value)
    table.add_row("Status", Text(result.status.value, style=style))
    table.add_row("Transferred", f"{format_count(result.completed_files)} files")
    table.add_row("Bytes", format_size(result.completed_bytes))
    table.add_row("Skipped", format_count(result.skipped_files))
    table.add_row("Failed", Text(format_count(result.failed_files), style=BAD if result.failed_files else OK))
    table.add_row("Retries", format_count(result.retries))
    table.add_row("Elapsed", format_duration(result.elapsed_seconds))
    table.add_row("Average", format_speed(result.average_speed_bps))
    console.print(Panel(table, title="Summary", border_style=style))

    if result.failures:
        failures = Table(title="Failed files", border_style=BAD, header_style=BAD)
        failures.add_column("Source", overflow="fold")
        failures.add_column("Error")
        failures.add_column("Message", overflow="fold")
        for failure in result.failures[:50]:
            failures.add_row(
                display_path(failure.source), failure.error_code, failure.message
            )
        console.print(failures)
        if len(result.failures) > 50:
            console.print(
                f"[{MUTED}]...and {len(result.failures) - 50:,} more. See the log file for the full list.[/]"
            )


def print_failures_json(result: TransferResult, path: Path) -> None:
    """Dump the failure list so it can be fed back in for a retry."""
    path.write_text(
        json.dumps([f.as_dict() for f in result.failures], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
