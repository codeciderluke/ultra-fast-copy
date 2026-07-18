"""Events the engine emits outward. No front-end types leak in here.

Progress is aggregated on a timer rather than emitted per file, so a job with a
million small files cannot drown its listener in callbacks.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .models import JobStatus, TransferResult


@dataclass(slots=True, frozen=True)
class ScanProgressEvent:
    """Emitted while the scanner walks the tree."""

    job_id: str
    scanned_files: int
    scanned_directories: int
    scanned_bytes: int
    current_directory: str | None = None
    done: bool = False


@dataclass(slots=True, frozen=True)
class ProgressEvent:
    """Aggregated transfer progress. `total_*` is None until a pre-scan finishes."""

    job_id: str
    completed_files: int
    total_files: int | None
    completed_bytes: int
    total_bytes: int | None
    current_file: str | None
    current_speed_bps: float
    average_speed_bps: float
    eta_seconds: float | None
    failed_files: int = 0
    skipped_files: int = 0

    @property
    def byte_fraction(self) -> float | None:
        if not self.total_bytes:
            return None
        return min(1.0, self.completed_bytes / self.total_bytes)

    @property
    def file_fraction(self) -> float | None:
        if not self.total_files:
            return None
        return min(1.0, self.completed_files / self.total_files)


@dataclass(slots=True, frozen=True)
class FileEvent:
    """A single file finished, was skipped, or failed."""

    job_id: str
    source: Path
    destination: Path
    size: int
    outcome: str  # "completed" | "skipped" | "failed"
    error_code: str | None = None
    message: str | None = None
    attempts: int = 1


@dataclass(slots=True, frozen=True)
class JobStateEvent:
    """The job moved to a new lifecycle state."""

    job_id: str
    status: JobStatus
    result: TransferResult | None = None


@dataclass(slots=True, frozen=True)
class LogEvent:
    """A human readable line for the log pane / log file."""

    job_id: str
    level: str
    message: str


Event = ScanProgressEvent | ProgressEvent | FileEvent | JobStateEvent | LogEvent
EventListener = Callable[[Event], None]


class EventEmitter:
    """Thread-safe fan-out to registered listeners.

    A listener that raises must not kill the transfer thread, so exceptions are
    swallowed here deliberately -- this is the one place where that is correct.
    """

    def __init__(self) -> None:
        self._listeners: list[EventListener] = []
        self._lock = threading.Lock()

    def subscribe(self, listener: EventListener) -> Callable[[], None]:
        with self._lock:
            self._listeners.append(listener)

        def unsubscribe() -> None:
            with self._lock:
                if listener in self._listeners:
                    self._listeners.remove(listener)

        return unsubscribe

    def emit(self, event: Event) -> None:
        with self._lock:
            listeners = tuple(self._listeners)
        for listener in listeners:
            try:
                listener(event)
            except Exception:
                continue


@dataclass(slots=True)
class ProgressAggregator:
    """Collects per-file completions and emits a ProgressEvent at most every `interval`.

    All mutating methods are called from worker threads, so every field update
    happens under the lock.
    """

    job_id: str
    emitter: EventEmitter
    interval: float = 0.2
    total_files: int | None = None
    total_bytes: int | None = None
    completed_files: int = 0
    completed_bytes: int = 0
    failed_files: int = 0
    skipped_files: int = 0
    current_file: str | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _start_time: float = field(default_factory=time.monotonic, repr=False)
    _last_emit: float = 0.0
    _last_emit_bytes: int = 0
    _current_speed: float = 0.0

    def set_totals(self, total_files: int | None, total_bytes: int | None) -> None:
        with self._lock:
            self.total_files = total_files
            self.total_bytes = total_bytes
        self.flush()

    def add_totals(self, files: int, size: int) -> None:
        """Streaming mode: totals grow as the scanner discovers work."""
        with self._lock:
            self.total_files = (self.total_files or 0) + files
            self.total_bytes = (self.total_bytes or 0) + size

    def file_started(self, path: Path) -> None:
        with self._lock:
            self.current_file = str(path)
        self._maybe_emit()

    def bytes_advanced(self, count: int) -> None:
        with self._lock:
            self.completed_bytes += count
        self._maybe_emit()

    def file_completed(self, size: int, *, count_bytes: bool = False) -> None:
        with self._lock:
            self.completed_files += 1
            if count_bytes:
                self.completed_bytes += size
        self._maybe_emit()

    def file_skipped(self) -> None:
        with self._lock:
            self.skipped_files += 1
        self._maybe_emit()

    def file_failed(self) -> None:
        with self._lock:
            self.failed_files += 1
        self._maybe_emit()

    def _maybe_emit(self) -> None:
        now = time.monotonic()
        with self._lock:
            if now - self._last_emit < self.interval:
                return
        self.flush()

    def flush(self) -> None:
        """Force an emit regardless of the interval (job start, end, totals known)."""
        self.emitter.emit(self.snapshot())

    def snapshot(self) -> ProgressEvent:
        now = time.monotonic()
        with self._lock:
            window = now - self._last_emit if self._last_emit else 0.0
            if window >= 0.05:
                delta = self.completed_bytes - self._last_emit_bytes
                self._current_speed = max(0.0, delta / window)
                self._last_emit_bytes = self.completed_bytes
            self._last_emit = now

            elapsed = max(1e-6, now - self._start_time)
            average = self.completed_bytes / elapsed
            eta = self._eta_locked(average)
            return ProgressEvent(
                job_id=self.job_id,
                completed_files=self.completed_files,
                total_files=self.total_files,
                completed_bytes=self.completed_bytes,
                total_bytes=self.total_bytes,
                current_file=self.current_file,
                current_speed_bps=self._current_speed,
                average_speed_bps=average,
                eta_seconds=eta,
                failed_files=self.failed_files,
                skipped_files=self.skipped_files,
            )

    def _eta_locked(self, average_bps: float) -> float | None:
        """Bytes-based ETA, falling back to file counts for tiny-file jobs."""
        if self.total_bytes and average_bps > 1.0:
            remaining = max(0, self.total_bytes - self.completed_bytes)
            return remaining / average_bps
        if self.total_files and self.completed_files > 0:
            elapsed = max(1e-6, time.monotonic() - self._start_time)
            per_file = elapsed / self.completed_files
            return max(0, self.total_files - self.completed_files) * per_file
        return None

    @property
    def elapsed_seconds(self) -> float:
        return time.monotonic() - self._start_time
