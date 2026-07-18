"""Main window: source tree on the left, destination tree on the right.

Transfers start by dropping a selection onto the destination pane or by pressing
the transfer button between the panes. Copy is the default operation.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import Qt, Slot
from PySide6.QtGui import QCloseEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QButtonGroup,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QSplitter,
    QStatusBar,
    QTableView,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .. import APP_NAME, __version__
from ..config.settings import AppSettings, load_settings, save_settings
from ..core.events import FileEvent, ProgressEvent, ScanProgressEvent
from ..core.models import JobStatus, OperationType, TransferOptions, TransferResult
from ..core.planner import validate_job
from ..utils.formatting import format_count, format_size
from ..utils.logging import default_log_file
from ..utils.paths import display_path
from ..utils.system import describe_pair
from .file_pane import FilePane
from .icon import app_icon, logo_pixmap
from .models import FailureTableModel, JobQueueModel, QueuedJob
from .settings_dialog import SettingsDialog
from .theme import Colors, level_color
from .transfer_widget import TransferPanel
from .worker import TransferWorker

MAX_LOG_LINES = 500


class MainWindow(QMainWindow):
    """Dual-pane transfer window."""

    def __init__(self, settings: AppSettings | None = None) -> None:
        super().__init__()
        self._settings = settings or load_settings()
        self._options: TransferOptions = self._settings.transfer
        self._worker: TransferWorker | None = None
        self._current_row: int | None = None

        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(app_icon())
        self.setMinimumSize(1100, 720)

        self._build_ui()
        self._restore_paths()
        self._wire_shortcuts()
        self._update_transfer_button()

    # -- construction ------------------------------------------------------

    def _build_ui(self) -> None:
        central = QWidget(self)
        self.setCentralWidget(central)

        self._source_pane = FilePane("Source", is_destination=False, parent=self)
        self._destination_pane = FilePane("Destination", is_destination=True, parent=self)
        self._destination_pane.transferRequested.connect(self._on_drop_transfer)
        self._source_pane.selectionChanged.connect(self._update_transfer_button)
        self._destination_pane.pathChanged.connect(lambda *_: self._update_transfer_button())

        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        splitter.addWidget(self._source_pane)
        splitter.addWidget(self._middle_column())
        splitter.addWidget(self._destination_pane)
        splitter.setSizes([460, 120, 460])
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)
        splitter.setStretchFactor(2, 1)
        splitter.setCollapsible(1, False)

        self._panel = TransferPanel(self)
        self._panel.startRequested.connect(self._start_from_selection)
        self._panel.pauseRequested.connect(self._pause)
        self._panel.resumeRequested.connect(self._resume)
        self._panel.cancelRequested.connect(self._cancel)

        layout = QVBoxLayout(central)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(12)
        layout.addWidget(self._header())
        layout.addWidget(splitter, 3)
        layout.addWidget(self._panel)
        layout.addWidget(self._tabs(), 1)

        self.setStatusBar(QStatusBar(self))
        self.statusBar().showMessage(f"{APP_NAME} {__version__}  ·  Log: {default_log_file()}")
        credit = QLabel("Designed by Codecider Lab", self)
        credit.setObjectName("Credit")
        self.statusBar().addPermanentWidget(credit)

    def _header(self) -> QWidget:
        logo = QLabel(self)
        logo.setPixmap(logo_pixmap(30))
        logo.setFixedSize(30, 30)
        logo.setScaledContents(True)

        title = QLabel(APP_NAME, self)
        title.setObjectName("AppTitle")
        subtitle = QLabel("High volume file transfer", self)
        subtitle.setObjectName("AppSubtitle")

        text = QVBoxLayout()
        text.setSpacing(0)
        text.addWidget(title)
        text.addWidget(subtitle)

        options_button = QPushButton("Options", self)
        options_button.clicked.connect(self._open_settings)

        swap_button = QPushButton("⇄ Swap", self)
        swap_button.setToolTip("Swap the source and destination paths")
        swap_button.clicked.connect(self._swap_panes)

        header = QHBoxLayout()
        header.setSpacing(12)
        header.addWidget(logo)
        header.addLayout(text)
        header.addSpacing(20)
        header.addWidget(self._operation_selector())
        header.addStretch(1)
        header.addWidget(swap_button)
        header.addWidget(options_button)

        container = QWidget(self)
        container.setLayout(header)
        return container

    def _operation_selector(self) -> QWidget:
        self._copy_radio = QRadioButton("Copy", self)
        self._copy_radio.setChecked(True)  # copy is the default
        self._move_radio = QRadioButton("Move", self)

        for button, radius in (
            (self._copy_radio, "border-top-left-radius: 6px; border-bottom-left-radius: 6px;"),
            (
                self._move_radio,
                "border-top-right-radius: 6px; border-bottom-right-radius: 6px; border-left: none;",
            ),
        ):
            button.setObjectName("Segment")
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setStyleSheet(f"QRadioButton {{ {radius} }}")

        self._operation_group = QButtonGroup(self)
        self._operation_group.addButton(self._copy_radio)
        self._operation_group.addButton(self._move_radio)
        self._operation_group.buttonToggled.connect(lambda *_: self._update_transfer_button())

        row = QHBoxLayout()
        row.setSpacing(0)
        row.addWidget(self._copy_radio)
        row.addWidget(self._move_radio)

        container = QWidget(self)
        container.setLayout(row)
        return container

    def _middle_column(self) -> QWidget:
        self._transfer_button = QPushButton("Copy →", self)
        self._transfer_button.setObjectName("Primary")
        self._transfer_button.setMinimumHeight(44)
        # Wide enough for the longest label ("Move → (12)") without clipping.
        self._transfer_button.setMinimumWidth(104)
        self._transfer_button.setToolTip("Transfer the selected items to the destination")
        self._transfer_button.clicked.connect(self._start_from_selection)

        hint = QLabel("or drag items\nto the right", self)
        hint.setObjectName("PaneHint")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)

        layout = QVBoxLayout()
        layout.setContentsMargins(4, 0, 4, 0)
        layout.addStretch(1)
        layout.addWidget(self._transfer_button)
        layout.addSpacing(8)
        layout.addWidget(hint)
        layout.addStretch(1)

        container = QWidget(self)
        container.setLayout(layout)
        container.setMinimumWidth(120)
        return container

    def _tabs(self) -> QWidget:
        self._log = QPlainTextEdit(self)
        self._log.setObjectName("Log")
        self._log.setReadOnly(True)
        self._log.setMaximumBlockCount(MAX_LOG_LINES)

        self._failure_model = FailureTableModel(self)
        self._failure_table = QTableView(self)
        self._failure_table.setModel(self._failure_model)
        self._failure_table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self._failure_table.verticalHeader().setVisible(False)
        for column in (0, 2):
            self._failure_table.horizontalHeader().setSectionResizeMode(
                column, QHeaderView.ResizeMode.Stretch
            )

        self._queue_model = JobQueueModel(self)
        self._queue_table = QTableView(self)
        self._queue_table.setModel(self._queue_model)
        self._queue_table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self._queue_table.verticalHeader().setVisible(False)
        self._queue_table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.Stretch
        )

        self._tab_widget = QTabWidget(self)
        self._tab_widget.addTab(self._log, "Log")
        self._tab_widget.addTab(self._failure_table, "Failed files")
        self._tab_widget.addTab(self._queue_table, "Queue")
        return self._tab_widget

    def _wire_shortcuts(self) -> None:
        QShortcut(QKeySequence("F5"), self, self._refresh_panes)
        QShortcut(QKeySequence("Ctrl+Return"), self, self._start_from_selection)
        QShortcut(QKeySequence("Escape"), self, self._cancel)

    def _restore_paths(self) -> None:
        ui = self._settings.ui
        if ui.last_source:
            self._source_pane.set_path(Path(ui.last_source))
        if ui.last_destination:
            self._destination_pane.set_path(Path(ui.last_destination))

    # -- operation ---------------------------------------------------------

    @property
    def operation(self) -> OperationType:
        return OperationType.COPY if self._copy_radio.isChecked() else OperationType.MOVE

    def preselect(self, path: Path) -> None:
        """Open with `path` selected in the source pane (used by the shell menu)."""
        self._source_pane.reveal(path)

    def _update_transfer_button(self) -> None:
        count = len(self._source_pane.selected_paths())
        verb = "Copy" if self.operation is OperationType.COPY else "Move"
        self._transfer_button.setText(f"{verb} →" if count <= 1 else f"{verb} → ({count})")
        self._transfer_button.setEnabled(count > 0 and self._worker is None)
        self._panel.start_button.setText(f"Start {verb.lower()}")

    def _swap_panes(self) -> None:
        source = self._source_pane.current_path
        destination = self._destination_pane.current_path
        self._source_pane.set_path(destination)
        self._destination_pane.set_path(source)

    def _refresh_panes(self) -> None:
        self._source_pane.refresh()
        self._destination_pane.refresh()

    def _open_settings(self) -> None:
        dialog = SettingsDialog(self._options, self)
        if dialog.exec():
            self._options = dialog.options
            self._append_log("INFO", "Transfer options updated.")

    # -- starting a transfer ----------------------------------------------

    @Slot(list, Path)
    def _on_drop_transfer(self, sources: list[Path], destination: Path) -> None:
        self._start(sources, destination)

    def _start_from_selection(self) -> None:
        sources = self._source_pane.selected_paths()
        if not sources:
            self._warn("Select the items to transfer in the source tree.")
            return
        self._start(sources, self._destination_pane.current_path)

    def _start(self, sources: list[Path], destination: Path) -> None:
        if self._worker is not None:
            self._warn("A transfer is already running. Wait for it to finish.")
            return
        if not sources:
            return

        options = replace(self._options, operation=self.operation)
        validation = validate_job(sources, destination, options)
        for warning in validation.warnings:
            self._append_log("WARNING", warning)
        if not validation.ok:
            self._error("\n".join(validation.errors))
            return

        if not self._confirm(sources, destination, options):
            return

        self._failure_model.clear()
        self._panel.reset()
        self._append_log(
            "INFO",
            f"{options.operation.value}: {len(sources)} item(s) -> "
            f"{display_path(destination)} [{describe_pair(sources[0], destination)}]",
        )

        row = self._queue_model.add(
            QueuedJob(
                sources=tuple(sources),
                destination=destination,
                operation=options.operation,
                status=JobStatus.SCANNING,
            )
        )
        self._current_row = row

        worker = TransferWorker(sources, destination, options, self)
        worker.progress.connect(self._on_progress)
        worker.scanProgress.connect(self._on_scan)
        worker.fileEvent.connect(self._on_file_event)
        worker.logMessage.connect(self._append_log)
        worker.stateChanged.connect(self._on_state)
        worker.finishedWithResult.connect(self._on_finished)
        worker.finished.connect(self._on_thread_done)
        self._worker = worker

        self._update_transfer_button()
        worker.start()

    def _confirm(self, sources: list[Path], destination: Path, options: TransferOptions) -> bool:
        """Moves delete data, so they get a confirmation; copies do not."""
        if options.operation is not OperationType.MOVE:
            return True
        names = "\n".join(f"  • {display_path(s)}" for s in sources[:5])
        if len(sources) > 5:
            names += f"\n  … and {len(sources) - 5} more"
        answer = QMessageBox.question(
            self,
            "Confirm move",
            f"These items will be moved and the originals deleted:\n\n{names}\n\n"
            f"Destination: {display_path(destination)}\n\n"
            "Across volumes, an original is deleted only after its copy is verified.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer is QMessageBox.StandardButton.Yes

    # -- worker signals ----------------------------------------------------

    @Slot(object)
    def _on_progress(self, event: ProgressEvent) -> None:
        self._panel.set_progress(event)

    @Slot(object)
    def _on_scan(self, event: ScanProgressEvent) -> None:
        self._panel.set_scanning(event)
        if event.done and self._current_row is not None:
            self._queue_model.update_totals(
                self._current_row, event.scanned_files, event.scanned_bytes
            )

    @Slot(object)
    def _on_file_event(self, event: FileEvent) -> None:
        self._failure_model.add_event(event)

    @Slot(str)
    def _on_state(self, status_value: str) -> None:
        status = JobStatus(status_value)
        self._panel.set_status(status)
        if self._current_row is not None:
            self._queue_model.update_status(self._current_row, status)

    @Slot(object)
    def _on_finished(self, result: TransferResult) -> None:
        self._panel.set_result(result)
        self._failure_model.set_failures(result.failures)
        if self._current_row is not None:
            self._queue_model.update_status(self._current_row, result.status)

        self.statusBar().showMessage(
            f"{format_count(result.completed_files)} files · "
            f"{format_size(result.completed_bytes)} · {result.failed_files} failed",
            15000,
        )
        self._destination_pane.refresh()
        if result.operation is OperationType.MOVE:
            self._source_pane.refresh()
        if result.failures:
            self._tab_widget.setCurrentWidget(self._failure_table)

    @Slot()
    def _on_thread_done(self) -> None:
        self._worker = None
        self._current_row = None
        self._update_transfer_button()

    # -- transport ---------------------------------------------------------

    def _pause(self) -> None:
        if self._worker is not None:
            self._worker.pause()

    def _resume(self) -> None:
        if self._worker is not None:
            self._worker.resume()

    def _cancel(self) -> None:
        if self._worker is None:
            return
        self._append_log("WARNING", "Cancelling; finishing the current file...")
        self._worker.cancel()

    # -- helpers -----------------------------------------------------------

    @Slot(str, str)
    def _append_log(self, level: str, message: str) -> None:
        self._log.appendHtml(
            f'<span style="color:{Colors.TEXT_DIM}">{level:<7}</span> '
            f'<span style="color:{level_color(level)}">{_escape(message)}</span>'
        )

    def _warn(self, message: str) -> None:
        QMessageBox.warning(self, APP_NAME, message)

    def _error(self, message: str) -> None:
        QMessageBox.critical(self, APP_NAME, message)
        self._append_log("ERROR", message.replace("\n", " "))

    def closeEvent(self, event: QCloseEvent) -> None:
        """A running transfer must not be killed silently."""
        if self._worker is not None and self._worker.isRunning():
            answer = QMessageBox.question(
                self,
                "Transfer in progress",
                "A transfer is running. Cancel it and quit?\n"
                "Finished files are kept, and a checkpointed job can be resumed later.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer is not QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            if not self._worker.stop_and_wait():
                self._append_log("ERROR", "The transfer thread did not stop in time.")

        self._save_settings()
        event.accept()

    def _save_settings(self) -> None:
        try:
            self._settings.transfer = self._options
            self._settings.ui.last_source = str(self._source_pane.current_path)
            self._settings.ui.last_destination = str(self._destination_pane.current_path)
            save_settings(self._settings)
        except OSError:
            pass  # never block exit on a config write


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
