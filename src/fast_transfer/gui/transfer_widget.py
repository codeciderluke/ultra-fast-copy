"""Bottom panel: progress bars, live stats, transport controls."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..core.events import ProgressEvent, ScanProgressEvent
from ..core.models import JobStatus, TransferResult
from ..utils.formatting import (
    format_count,
    format_duration,
    format_size,
    format_speed,
    truncate_middle,
)
from ..utils.paths import display_path
from .theme import Colors, status_color

STATUS_LABELS: dict[JobStatus, str] = {
    JobStatus.PENDING: "Idle",
    JobStatus.SCANNING: "Scanning",
    JobStatus.RUNNING: "Transferring",
    JobStatus.PAUSED: "Paused",
    JobStatus.COMPLETED: "Completed",
    JobStatus.COMPLETED_WITH_ERRORS: "Completed with errors",
    JobStatus.FAILED: "Failed",
    JobStatus.CANCELLED: "Cancelled",
}


class StatTile(QWidget):
    """One labelled number in the stat row."""

    def __init__(self, label: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._value = QLabel("-", self)
        self._value.setObjectName("StatValue")
        caption = QLabel(label, self)
        caption.setObjectName("StatLabel")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(1)
        layout.addWidget(self._value)
        layout.addWidget(caption)

    def set_value(self, text: str, color: str | None = None) -> None:
        self._value.setText(text)
        self._value.setStyleSheet(f"color: {color};" if color else "")


class TransferPanel(QFrame):
    """Progress, stats, and the start/pause/cancel controls."""

    startRequested = Signal()
    pauseRequested = Signal()
    resumeRequested = Signal()
    cancelRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self._paused = False

        self._status_label = QLabel("Idle", self)
        self._status_label.setObjectName("PaneTitle")

        self._current_file = QLabel("-", self)
        self._current_file.setObjectName("CurrentFile")
        self._current_file.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        self._overall = QProgressBar(self)
        self._overall.setRange(0, 1000)  # per-mille, so the bar moves on big jobs
        self._overall.setValue(0)
        self._overall.setTextVisible(False)

        self._percent = QLabel("0.0%", self)
        self._percent.setObjectName("StatValue")

        self._tiles = {
            "files": StatTile("Files", self),
            "size": StatTile("Transferred", self),
            "speed": StatTile("Speed", self),
            "average": StatTile("Average", self),
            "eta": StatTile("Remaining", self),
            "failed": StatTile("Failed", self),
        }

        self._start = QPushButton("Start", self)
        self._start.setObjectName("Primary")
        self._start.clicked.connect(self.startRequested)

        self._pause = QPushButton("Pause", self)
        self._pause.setEnabled(False)
        self._pause.clicked.connect(self._toggle_pause)

        self._cancel = QPushButton("Cancel", self)
        self._cancel.setObjectName("Danger")
        self._cancel.setEnabled(False)
        self._cancel.clicked.connect(self.cancelRequested)

        self._build_layout()
        self.reset()

    def _build_layout(self) -> None:
        top = QHBoxLayout()
        top.addWidget(self._status_label)
        top.addStretch(1)
        top.addWidget(self._percent)

        stats = QGridLayout()
        stats.setHorizontalSpacing(24)
        for column, key in enumerate(("files", "size", "speed", "average", "eta", "failed")):
            stats.addWidget(self._tiles[key], 0, column)
        stats.setColumnStretch(6, 1)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        buttons.addStretch(1)
        buttons.addWidget(self._pause)
        buttons.addWidget(self._cancel)
        buttons.addWidget(self._start)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)
        layout.addLayout(top)
        layout.addWidget(self._overall)
        layout.addWidget(self._current_file)
        layout.addLayout(stats)
        layout.addLayout(buttons)

    # -- state -------------------------------------------------------------

    def reset(self) -> None:
        self._paused = False
        self._overall.setRange(0, 1000)
        self._overall.setValue(0)
        self._percent.setText("0.0%")
        self._status_label.setText("Idle")
        self._status_label.setStyleSheet(f"color: {Colors.TEXT};")
        self._current_file.setText("-")
        for key, tile in self._tiles.items():
            tile.set_value("0" if key in ("files", "failed") else "-")
        self._set_running(False)

    def set_scanning(self, event: ScanProgressEvent) -> None:
        self._status_label.setText("Scanning")
        self._status_label.setStyleSheet(f"color: {Colors.ACCENT};")
        self._overall.setRange(0, 0)  # busy: total not known yet
        self._tiles["files"].set_value(format_count(event.scanned_files))
        self._tiles["size"].set_value(format_size(event.scanned_bytes))
        if event.current_directory:
            self._current_file.setText(truncate_middle(display_path(event.current_directory), 90))
        if event.done:
            self._overall.setRange(0, 1000)

    def set_progress(self, event: ProgressEvent) -> None:
        fraction = event.byte_fraction
        if fraction is None:
            fraction = event.file_fraction
        if fraction is None:
            self._overall.setRange(0, 0)
        else:
            self._overall.setRange(0, 1000)
            self._overall.setValue(int(fraction * 1000))
            self._percent.setText(f"{fraction * 100:.1f}%")

        files = format_count(event.completed_files)
        if event.total_files:
            files = f"{files} / {format_count(event.total_files)}"
        self._tiles["files"].set_value(files)

        size = format_size(event.completed_bytes)
        if event.total_bytes:
            size = f"{size} / {format_size(event.total_bytes)}"
        self._tiles["size"].set_value(size)

        self._tiles["speed"].set_value(format_speed(event.current_speed_bps))
        self._tiles["average"].set_value(format_speed(event.average_speed_bps))
        self._tiles["eta"].set_value(format_duration(event.eta_seconds))
        self._tiles["failed"].set_value(
            format_count(event.failed_files), Colors.ERROR if event.failed_files else None
        )
        if event.current_file:
            self._current_file.setText(truncate_middle(display_path(event.current_file), 90))

    def set_status(self, status: JobStatus) -> None:
        self._status_label.setText(STATUS_LABELS.get(status, status.value))
        self._status_label.setStyleSheet(f"color: {status_color(status.value)};")

        if status in (JobStatus.RUNNING, JobStatus.SCANNING):
            self._set_running(True)
        elif status is JobStatus.PAUSED:
            self._paused = True
            self._pause.setText("Resume")

    def set_result(self, result: TransferResult) -> None:
        self.set_status(result.status)
        self._set_running(False)
        self._current_file.setText(
            f"{format_count(result.completed_files)} files · "
            f"{format_size(result.completed_bytes)} · "
            f"{format_duration(result.elapsed_seconds)} · "
            f"avg {format_speed(result.average_speed_bps)}"
        )
        if result.status is JobStatus.COMPLETED:
            self._overall.setRange(0, 1000)
            self._overall.setValue(1000)
            self._percent.setText("100.0%")
        self._tiles["failed"].set_value(
            format_count(result.failed_files), Colors.ERROR if result.failed_files else None
        )

    def _set_running(self, running: bool) -> None:
        self._start.setEnabled(not running)
        self._pause.setEnabled(running)
        self._cancel.setEnabled(running)
        if not running:
            self._paused = False
            self._pause.setText("Pause")

    def _toggle_pause(self) -> None:
        if self._paused:
            self._paused = False
            self._pause.setText("Pause")
            self.resumeRequested.emit()
        else:
            self._paused = True
            self._pause.setText("Resume")
            self.pauseRequested.emit()

    @property
    def start_button(self) -> QPushButton:
        return self._start
