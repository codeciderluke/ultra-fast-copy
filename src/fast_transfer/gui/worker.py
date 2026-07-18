"""QThread adapter: runs the engine off the GUI thread, re-emitting events as signals.

Qt queues signals across the thread boundary, so slots always run on the GUI
thread. Progress is throttled to ~8/s: a fast local copy out-runs repainting.
"""

from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal

from ..core.control import TransferControl
from ..core.engine import TransferEngine
from ..core.events import (
    Event,
    FileEvent,
    JobStateEvent,
    LogEvent,
    ProgressEvent,
    ScanProgressEvent,
)
from ..core.models import ConflictPolicy, JobStatus, TransferJob, TransferOptions, TransferResult

UI_EVENT_INTERVAL = 0.125  # seconds; ~8 UI updates per second


class TransferWorker(QThread):
    """Runs one `TransferJob`. Create a new worker per job."""

    progress = Signal(object)  # ProgressEvent
    scanProgress = Signal(object)  # ScanProgressEvent
    fileEvent = Signal(object)  # FileEvent
    logMessage = Signal(str, str)  # level, message
    stateChanged = Signal(str)  # JobStatus value
    finishedWithResult = Signal(object)  # TransferResult
    conflictRaised = Signal(str, str)  # source, destination

    def __init__(
        self,
        sources: list[Path],
        destination: Path,
        options: TransferOptions,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.job = TransferJob(
            sources=tuple(Path(s) for s in sources),
            destination=Path(destination),
            options=options,
        )
        self.control = TransferControl()
        self._engine: TransferEngine | None = None
        self._last_progress = 0.0
        self._last_scan = 0.0
        self.result: TransferResult | None = None

    @property
    def job_id(self) -> str:
        return self.job.job_id

    def run(self) -> None:
        """QThread entry point. Never touches a widget."""
        engine = TransferEngine(self.job, control=self.control)
        self._engine = engine
        engine.emitter.subscribe(self._on_event)
        try:
            self.result = engine.run()
        except Exception as exc:
            self.logMessage.emit("ERROR", f"Unexpected engine error: {exc}")
            self.stateChanged.emit(JobStatus.FAILED.value)
            return
        self.finishedWithResult.emit(self.result)

    def _on_event(self, event: Event) -> None:
        """Called from engine threads: only emit signals, never block."""
        now = time.monotonic()
        match event:
            case ProgressEvent():
                if now - self._last_progress < UI_EVENT_INTERVAL:
                    return
                self._last_progress = now
                self.progress.emit(event)
            case ScanProgressEvent():
                if not event.done and now - self._last_scan < UI_EVENT_INTERVAL:
                    return
                self._last_scan = now
                self.scanProgress.emit(event)
            case FileEvent():
                # Only failures are pushed to the UI; a completed-file signal per
                # file would flood the queue on a million-file job.
                if event.outcome == "failed":
                    self.fileEvent.emit(event)
            case JobStateEvent():
                self.stateChanged.emit(event.status.value)
            case LogEvent():
                self.logMessage.emit(event.level, event.message)

    # -- controls (called from the GUI thread) -----------------------------

    def pause(self) -> None:
        if self._engine is not None:
            self._engine.pause()

    def resume(self) -> None:
        if self._engine is not None:
            self._engine.resume()

    def cancel(self) -> None:
        self.control.cancel()

    @property
    def is_paused(self) -> bool:
        return self.control.paused

    def stop_and_wait(self, timeout_ms: int = 8000) -> bool:
        """Cancel and join. Returns False if the thread outlived the timeout."""
        self.cancel()
        return self.wait(timeout_ms)


class ConflictPrompt(QObject):
    """Bridges the engine's blocking `ask` callback to a GUI-thread dialog.

    Not wired into the default flow: `ask` blocks a worker until the user
    answers, which is miserable for thousands of conflicts.
    """

    ask = Signal(object, object)  # source, destination

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._answer: ConflictPolicy = ConflictPolicy.SKIP
        self._answered = False

    def set_answer(self, policy: ConflictPolicy) -> None:
        self._answer = policy
        self._answered = True

    def __call__(self, source: Path, destination: Path) -> ConflictPolicy:
        self._answered = False
        self.ask.emit(source, destination)
        deadline = time.monotonic() + 60
        while not self._answered and time.monotonic() < deadline:
            time.sleep(0.05)
        return self._answer if self._answered else ConflictPolicy.SKIP
