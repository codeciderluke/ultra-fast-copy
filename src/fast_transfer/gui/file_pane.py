"""One side of the dual-pane browser: drive picker, path bar, file tree.

Drops are intercepted here instead of being handled by QFileSystemModel, whose
built-in drop support would copy files with Qt's own implementation.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QDir, QItemSelectionModel, QModelIndex, QPoint, Qt, Signal
from PySide6.QtGui import (
    QDragEnterEvent,
    QDragLeaveEvent,
    QDragMoveEvent,
    QDropEvent,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QFileSystemModel,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from ..utils.paths import IS_WINDOWS


class FileTreeView(QTreeView):
    """Tree that can start drags and accept them, but never moves files itself."""

    pathsDropped = Signal(list, Path)  # sources, destination directory

    def __init__(self, *, accept_drops: bool, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._accepts = accept_drops
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setUniformRowHeights(True)  # keeps large folders fast
        self.setSortingEnabled(True)
        self.setAnimated(False)
        self.setDragEnabled(True)
        self.setAcceptDrops(accept_drops)
        self.setDropIndicatorShown(False)
        self.setDragDropMode(
            QAbstractItemView.DragDropMode.DragDrop
            if accept_drops
            else QAbstractItemView.DragDropMode.DragOnly
        )
        self.setDefaultDropAction(Qt.DropAction.CopyAction)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.setProperty("dropActive", False)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if self._accepts and event.mimeData().hasUrls():
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
            self._set_drop_active(True)
        else:
            event.ignore()

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        if self._accepts and event.mimeData().hasUrls():
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
        else:
            event.ignore()

    def dragLeaveEvent(self, event: QDragLeaveEvent) -> None:
        self._set_drop_active(False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:
        self._set_drop_active(False)
        if not self._accepts or not event.mimeData().hasUrls():
            event.ignore()
            return

        sources = self._unique_local_paths(event)
        destination = self._drop_directory(event.position().toPoint())
        if not sources or destination is None:
            event.ignore()
            return

        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()
        # No super().dropEvent(): the engine performs the transfer.
        self.pathsDropped.emit(sources, destination)

    @staticmethod
    def _unique_local_paths(event: QDropEvent) -> list[Path]:
        # A tree selection spans every column, so each file can arrive once per column.
        seen: dict[str, Path] = {}
        for url in event.mimeData().urls():
            if url.isLocalFile():
                local = url.toLocalFile()
                seen.setdefault(local.casefold(), Path(local))
        return list(seen.values())

    def _drop_directory(self, position: QPoint) -> Path | None:
        """Folder under the cursor, or the tree root when dropped on empty space."""
        index = self.indexAt(position)
        model = self.model()
        if not isinstance(model, QFileSystemModel):
            return None
        if not index.isValid():
            root = self.rootIndex()
            return Path(model.filePath(root)) if root.isValid() else None
        path = Path(model.filePath(index))
        return path if path.is_dir() else path.parent

    def _set_drop_active(self, active: bool) -> None:
        if self.property("dropActive") == active:
            return
        self.setProperty("dropActive", active)
        # Re-polish so the [dropActive] style rule applies now.
        self.style().unpolish(self)
        self.style().polish(self)


class LocalizedFileSystemModel(QFileSystemModel):
    """QFileSystemModel with explicit column headers."""

    HEADERS = ("Name", "Size", "Type", "Modified")

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> object:
        if (
            orientation is Qt.Orientation.Horizontal
            and role == Qt.ItemDataRole.DisplayRole
            and 0 <= section < len(self.HEADERS)
        ):
            return self.HEADERS[section]
        return super().headerData(section, orientation, role)


class FilePane(QFrame):
    """Drive picker + path bar + file tree."""

    transferRequested = Signal(list, Path)  # sources, destination
    pathChanged = Signal(Path)
    selectionChanged = Signal()

    def __init__(
        self,
        title: str,
        *,
        is_destination: bool,
        start_path: Path | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self._is_destination = is_destination

        self._model = LocalizedFileSystemModel(self)
        self._model.setReadOnly(True)
        self._model.setRootPath("")
        self._model.setFilter(
            QDir.Filter.AllEntries | QDir.Filter.NoDotAndDotDot | QDir.Filter.Hidden
        )

        self._tree = FileTreeView(accept_drops=is_destination, parent=self)
        self._tree.setModel(self._model)
        self._tree.pathsDropped.connect(self.transferRequested)
        self._tree.customContextMenuRequested.connect(self._show_context_menu)
        self._tree.doubleClicked.connect(self._on_double_clicked)
        self._tree.selectionModel().selectionChanged.connect(
            lambda *_: self.selectionChanged.emit()
        )
        self._configure_columns()

        self._path_edit = QLineEdit(self)
        self._path_edit.setPlaceholderText("Type a path, or pick one below")
        self._path_edit.returnPressed.connect(
            lambda: self.set_path(Path(self._path_edit.text().strip()))
        )

        self._drive_combo = QComboBox(self)
        self._drive_combo.setFixedWidth(78)
        self._drive_combo.currentTextChanged.connect(self._on_drive_changed)
        self._populate_drives()

        self._build_layout(title)
        self.set_path(start_path or Path.home())

    # -- layout ------------------------------------------------------------

    def _build_layout(self, title: str) -> None:
        heading = QLabel(title, self)
        heading.setObjectName("PaneTitle")

        hint = QLabel(
            "Drop items here to transfer them"
            if self._is_destination
            else "Select items and drag them to the destination",
            self,
        )
        hint.setObjectName("PaneHint")

        header = QVBoxLayout()
        header.setSpacing(2)
        header.addWidget(heading)
        header.addWidget(hint)

        up_button = QPushButton("↑", self)
        up_button.setObjectName("Icon")
        up_button.setToolTip("Parent folder")
        up_button.clicked.connect(self.go_up)

        browse_button = QPushButton("...", self)
        browse_button.setObjectName("Icon")
        browse_button.setToolTip("Browse for a folder")
        browse_button.clicked.connect(self._browse)

        path_row = QHBoxLayout()
        path_row.setSpacing(6)
        path_row.addWidget(self._drive_combo)
        path_row.addWidget(self._path_edit, 1)
        path_row.addWidget(up_button)
        path_row.addWidget(browse_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        layout.addLayout(header)
        layout.addLayout(path_row)
        layout.addWidget(self._tree, 1)

    def _configure_columns(self) -> None:
        self._tree.setColumnWidth(0, 260)
        self._tree.hideColumn(2)  # "Type" duplicates the icon
        header = self._tree.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setStretchLastSection(False)
        self._tree.sortByColumn(0, Qt.SortOrder.AscendingOrder)

    def _populate_drives(self) -> None:
        self._drive_combo.blockSignals(True)
        self._drive_combo.clear()
        for drive in QDir.drives():
            self._drive_combo.addItem(drive.absolutePath())
        self._drive_combo.blockSignals(False)

    # -- public API --------------------------------------------------------

    @property
    def current_path(self) -> Path:
        root = self._tree.rootIndex()
        if root.isValid():
            return Path(self._model.filePath(root))
        return Path(self._path_edit.text() or Path.home())

    def set_path(self, path: Path) -> None:
        """Point the tree at `path`. Files resolve to their parent folder."""
        if not path or not str(path).strip():
            return
        target = path if path.is_dir() else path.parent
        if not target.exists():
            return

        index = self._model.setRootPath(str(target))
        self._tree.setRootIndex(index)
        self._path_edit.setText(str(target))
        self._sync_drive_combo(target)
        self.pathChanged.emit(target)

    def reveal(self, path: Path) -> None:
        """Show `path` inside its parent, selected and scrolled into view."""
        if not path or not path.exists():
            return
        parent = path.parent if path.parent != path else path
        self.set_path(parent)

        index = self._model.index(str(path))
        if not index.isValid():
            return
        self._tree.setCurrentIndex(index)
        self._tree.selectionModel().select(
            index,
            QItemSelectionModel.SelectionFlag.ClearAndSelect
            | QItemSelectionModel.SelectionFlag.Rows,
        )
        self._tree.scrollTo(index, QAbstractItemView.ScrollHint.PositionAtCenter)
        self.selectionChanged.emit()

    def selected_paths(self) -> list[Path]:
        """Selected rows, de-duplicated across columns."""
        seen: dict[str, Path] = {}
        for index in self._tree.selectionModel().selectedIndexes():
            if index.column() != 0:
                continue
            path = self._model.filePath(index)
            seen[path.casefold()] = Path(path)
        return list(seen.values())

    def refresh(self) -> None:
        current = self.current_path
        self._model.setRootPath("")
        self.set_path(current)

    def go_up(self) -> None:
        current = self.current_path
        if current.parent != current:
            self.set_path(current.parent)

    # -- internals ---------------------------------------------------------

    def _sync_drive_combo(self, path: Path) -> None:
        drive = str(path.anchor)
        if not drive:
            return
        self._drive_combo.blockSignals(True)
        for i in range(self._drive_combo.count()):
            if self._drive_combo.itemText(i).casefold() == drive.replace("\\", "/").casefold():
                self._drive_combo.setCurrentIndex(i)
                break
        self._drive_combo.blockSignals(False)

    def _on_drive_changed(self, text: str) -> None:
        if text:
            self.set_path(Path(text))

    def _on_double_clicked(self, index: QModelIndex) -> None:
        path = Path(self._model.filePath(index))
        if path.is_dir():
            self.set_path(path)

    def _browse(self) -> None:
        chosen = QFileDialog.getExistingDirectory(
            self, "Select a folder", str(self.current_path), QFileDialog.Option.ShowDirsOnly
        )
        if chosen:
            self.set_path(Path(chosen))

    def _show_context_menu(self, position: QPoint) -> None:
        menu = QMenu(self)
        open_action = menu.addAction("Open this folder")
        refresh_action = menu.addAction("Refresh")
        menu.addSeparator()
        explorer_action = menu.addAction("Show in Explorer")

        chosen = menu.exec(self._tree.viewport().mapToGlobal(position))
        if chosen is None:
            return

        index = self._tree.indexAt(position)
        target = Path(self._model.filePath(index)) if index.isValid() else self.current_path
        if chosen is open_action:
            self.set_path(target)
        elif chosen is refresh_action:
            self.refresh()
        elif chosen is explorer_action:
            self._open_in_explorer(target if target.is_dir() else target.parent)

    @staticmethod
    def _open_in_explorer(path: Path) -> None:
        if not IS_WINDOWS:
            return
        import contextlib
        import subprocess

        with contextlib.suppress(OSError):
            subprocess.Popen(["explorer", str(path)])
