"""Qt item models for the failure table and the job queue."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtGui import QColor

from ..core.events import FileEvent
from ..core.models import TERMINAL_STATUSES, FileFailure, JobStatus, OperationType
from ..utils.formatting import format_size
from ..utils.paths import display_path
from .theme import Colors, status_color

STATUS_LABELS: dict[JobStatus, str] = {
    JobStatus.PENDING: "Queued",
    JobStatus.SCANNING: "Scanning",
    JobStatus.RUNNING: "Running",
    JobStatus.PAUSED: "Paused",
    JobStatus.COMPLETED: "Completed",
    JobStatus.COMPLETED_WITH_ERRORS: "Completed with errors",
    JobStatus.FAILED: "Failed",
    JobStatus.CANCELLED: "Cancelled",
}


class FailureTableModel(QAbstractTableModel):
    """The failed-files list."""

    HEADERS = ("File", "Error", "Message", "Attempts")

    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)
        self._rows: list[FileFailure] = []

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.HEADERS)

    def headerData(
        self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole
    ) -> Any:
        if role != Qt.ItemDataRole.DisplayRole or orientation is not Qt.Orientation.Horizontal:
            return None
        return self.HEADERS[section]

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid():
            return None
        failure = self._rows[index.row()]

        if role == Qt.ItemDataRole.DisplayRole:
            match index.column():
                case 0:
                    return display_path(failure.source)
                case 1:
                    return failure.error_code
                case 2:
                    return failure.message
                case 3:
                    return str(failure.attempts)
        elif role == Qt.ItemDataRole.ToolTipRole:
            return (
                f"{display_path(failure.source)}\n"
                f"-> {display_path(failure.destination)}\n\n{failure.message}"
            )
        elif role == Qt.ItemDataRole.ForegroundRole and index.column() == 1:
            return QColor(Colors.ERROR)
        return None

    def add(self, failure: FileFailure) -> None:
        row = len(self._rows)
        self.beginInsertRows(QModelIndex(), row, row)
        self._rows.append(failure)
        self.endInsertRows()

    def add_event(self, event: FileEvent) -> None:
        self.add(
            FileFailure(
                source=event.source,
                destination=event.destination,
                error_code=event.error_code or "unknown",
                message=event.message or "",
                attempts=event.attempts,
            )
        )

    def set_failures(self, failures: list[FileFailure]) -> None:
        self.beginResetModel()
        self._rows = list(failures)
        self.endResetModel()

    def clear(self) -> None:
        self.beginResetModel()
        self._rows.clear()
        self.endResetModel()

    @property
    def failures(self) -> list[FileFailure]:
        return list(self._rows)

    def sources(self) -> list[Path]:
        return [f.source for f in self._rows]


@dataclass(slots=True)
class QueuedJob:
    """A job waiting its turn. Only one runs at a time in this version."""

    sources: tuple[Path, ...]
    destination: Path
    operation: OperationType
    status: JobStatus = JobStatus.PENDING
    total_files: int = 0
    total_bytes: int = 0
    completed_files: int = 0
    job_id: str = ""

    @property
    def label(self) -> str:
        if len(self.sources) == 1:
            return Path(self.sources[0]).name
        return f"{len(self.sources)} items"


class JobQueueModel(QAbstractTableModel):
    """The job queue table."""

    HEADERS = ("Operation", "Items", "Destination", "Size", "Status")

    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)
        self._rows: list[QueuedJob] = []

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.HEADERS)

    def headerData(
        self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole
    ) -> Any:
        if role != Qt.ItemDataRole.DisplayRole or orientation is not Qt.Orientation.Horizontal:
            return None
        return self.HEADERS[section]

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid():
            return None
        job = self._rows[index.row()]

        if role == Qt.ItemDataRole.DisplayRole:
            match index.column():
                case 0:
                    return "Copy" if job.operation is OperationType.COPY else "Move"
                case 1:
                    return job.label
                case 2:
                    return display_path(job.destination)
                case 3:
                    return format_size(job.total_bytes) if job.total_bytes else "-"
                case 4:
                    return STATUS_LABELS.get(job.status, job.status.value)
        elif role == Qt.ItemDataRole.ForegroundRole and index.column() == 4:
            return QColor(status_color(job.status.value))
        elif role == Qt.ItemDataRole.ToolTipRole:
            return "\n".join(display_path(s) for s in job.sources)
        return None

    def add(self, job: QueuedJob) -> int:
        row = len(self._rows)
        self.beginInsertRows(QModelIndex(), row, row)
        self._rows.append(job)
        self.endInsertRows()
        return row

    def update_status(self, row: int, status: JobStatus) -> None:
        if not 0 <= row < len(self._rows):
            return
        self._rows[row].status = status
        index = self.index(row, 4)
        self.dataChanged.emit(index, index)

    def update_totals(self, row: int, total_files: int, total_bytes: int) -> None:
        if not 0 <= row < len(self._rows):
            return
        self._rows[row].total_files = total_files
        self._rows[row].total_bytes = total_bytes
        self.dataChanged.emit(self.index(row, 3), self.index(row, 3))

    def next_pending(self) -> int | None:
        for row, job in enumerate(self._rows):
            if job.status is JobStatus.PENDING:
                return row
        return None

    def job_at(self, row: int) -> QueuedJob | None:
        return self._rows[row] if 0 <= row < len(self._rows) else None

    def remove(self, row: int) -> None:
        if not 0 <= row < len(self._rows):
            return
        self.beginRemoveRows(QModelIndex(), row, row)
        del self._rows[row]
        self.endRemoveRows()

    def clear_finished(self) -> None:
        self.beginResetModel()
        self._rows = [j for j in self._rows if j.status not in TERMINAL_STATUSES]
        self.endResetModel()

    @property
    def jobs(self) -> list[QueuedJob]:
        return list(self._rows)
