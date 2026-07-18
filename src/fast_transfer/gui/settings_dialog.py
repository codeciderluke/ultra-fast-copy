"""Options dialog: speed presets plus the full option surface."""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..config.defaults import detect_preset, preset_options
from ..core.models import (
    MIB,
    ConflictPolicy,
    ScanMode,
    SpeedPreset,
    SymlinkPolicy,
    TransferOptions,
    VerifyMode,
)
from ..utils.formatting import format_size

VERIFY_LABELS: list[tuple[VerifyMode, str]] = [
    (VerifyMode.NONE, "None - fastest, no check"),
    (VerifyMode.SIZE, "Size - compare file size"),
    (VerifyMode.MTIME_SIZE, "Modified time + size"),
    (VerifyMode.XXHASH, "xxHash - full hash (recommended)"),
    (VerifyMode.SHA256, "SHA-256 - full hash, slowest"),
]

CONFLICT_LABELS: list[tuple[ConflictPolicy, str]] = [
    (ConflictPolicy.SKIP, "Skip"),
    (ConflictPolicy.OVERWRITE, "Overwrite"),
    (ConflictPolicy.OVERWRITE_IF_NEWER, "Overwrite if the source is newer"),
    (ConflictPolicy.OVERWRITE_IF_DIFFERENT, "Overwrite if the contents differ"),
    (ConflictPolicy.RENAME, "Keep both, rename the new file"),
]

SYMLINK_LABELS: list[tuple[SymlinkPolicy, str]] = [
    (SymlinkPolicy.SKIP, "Skip"),
    (SymlinkPolicy.COPY_LINK, "Copy the link itself"),
    (SymlinkPolicy.FOLLOW, "Follow the link and copy its target"),
]

PRESET_LABELS: list[tuple[SpeedPreset, str]] = [
    (SpeedPreset.FAST, "Fast"),
    (SpeedPreset.BALANCED, "Balanced"),
    (SpeedPreset.SAFE, "Safe"),
    (SpeedPreset.CUSTOM, "Custom"),
]

BUFFER_CHOICES = [64 * 1024, 256 * 1024, 1 * MIB, 4 * MIB, 8 * MIB, 16 * MIB]


class SettingsDialog(QDialog):
    """Edits a `TransferOptions`. Read `options` after `exec()`."""

    def __init__(self, options: TransferOptions, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Transfer options")
        self.setMinimumWidth(560)
        self.options = replace(options)

        self._preset = QComboBox(self)
        for preset, label in PRESET_LABELS:
            self._preset.addItem(label, preset)
        self._preset.currentIndexChanged.connect(self._on_preset_changed)

        self._workers = QSpinBox(self)
        self._workers.setRange(0, 64)
        self._workers.setSpecialValueText("Auto")
        self._workers.setToolTip(
            "Auto picks a worker count from the source and destination devices\n"
            "and the average file size."
        )

        self._buffer = QComboBox(self)
        for size in BUFFER_CHOICES:
            self._buffer.addItem(format_size(size), size)

        self._verify = QComboBox(self)
        for mode, label in VERIFY_LABELS:
            self._verify.addItem(label, mode)

        self._conflict = QComboBox(self)
        for policy, label in CONFLICT_LABELS:
            self._conflict.addItem(label, policy)

        self._retry = QSpinBox(self)
        self._retry.setRange(0, 20)

        self._scan = QComboBox(self)
        self._scan.addItem("Pre-scan - exact progress", ScanMode.PRESCAN)
        self._scan.addItem("Streaming - start immediately", ScanMode.STREAMING)

        self._symlink = QComboBox(self)
        for policy, label in SYMLINK_LABELS:
            self._symlink.addItem(label, policy)

        self._bandwidth = QSpinBox(self)
        self._bandwidth.setRange(0, 10_000)
        self._bandwidth.setSuffix(" MiB/s")
        self._bandwidth.setSpecialValueText("Unlimited")

        self._hidden = QCheckBox("Include hidden files", self)
        self._system = QCheckBox("Include system files", self)
        self._times = QCheckBox("Preserve file times", self)
        self._permissions = QCheckBox("Preserve permissions", self)
        self._checkpoint = QCheckBox("Write a checkpoint (allows resume)", self)
        self._partial = QCheckBox("Use a temporary name until complete", self)
        self._partial.setToolTip(
            "Writes to .fasttransfer.partial and renames after verification,\n"
            "so an interrupted copy never leaves a truncated file."
        )
        self._dry_run = QCheckBox("Dry run (write nothing)", self)

        for widget in (
            self._workers,
            self._buffer,
            self._verify,
            self._conflict,
            self._retry,
            self._scan,
            self._hidden,
            self._times,
            self._checkpoint,
        ):
            self._connect_custom(widget)

        self._build_layout()
        self._load(self.options)

    def _connect_custom(self, widget: QWidget) -> None:
        """Any manual edit flips the preset selector to Custom."""
        if isinstance(widget, QComboBox):
            widget.activated.connect(lambda *_: self._mark_custom())
        elif isinstance(widget, QSpinBox):
            widget.valueChanged.connect(lambda *_: self._mark_custom())
        elif isinstance(widget, QCheckBox):
            widget.clicked.connect(lambda *_: self._mark_custom())

    def _build_layout(self) -> None:
        preset_row = QHBoxLayout()
        preset_row.addWidget(QLabel("Preset", self))
        preset_row.addWidget(self._preset, 1)

        performance = QGroupBox("Performance", self)
        performance_form = QFormLayout(performance)
        performance_form.addRow("Workers", self._workers)
        performance_form.addRow("Buffer size", self._buffer)
        performance_form.addRow("Scan mode", self._scan)
        performance_form.addRow("Bandwidth limit", self._bandwidth)

        safety = QGroupBox("Safety", self)
        safety_form = QFormLayout(safety)
        safety_form.addRow("Verification", self._verify)
        safety_form.addRow("On conflict", self._conflict)
        safety_form.addRow("Retries", self._retry)
        safety_form.addRow("Symlinks", self._symlink)

        behaviour = QGroupBox("Behaviour", self)
        behaviour_layout = QVBoxLayout(behaviour)
        for checkbox in (
            self._hidden,
            self._system,
            self._times,
            self._permissions,
            self._checkpoint,
            self._partial,
            self._dry_run,
        ):
            behaviour_layout.addWidget(checkbox)

        note = QLabel(
            "On a move across volumes, the original is deleted only after the copy "
            "has been verified.",
            self,
        )
        note.setObjectName("Muted")
        note.setWordWrap(True)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setObjectName("Primary")

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.addLayout(preset_row)
        layout.addWidget(performance)
        layout.addWidget(safety)
        layout.addWidget(behaviour)
        layout.addWidget(note)
        layout.addWidget(buttons)

    # -- data --------------------------------------------------------------

    def _load(self, options: TransferOptions) -> None:
        self._set_current(self._preset, detect_preset(options))
        self._workers.setValue(options.workers or 0)
        self._set_closest_buffer(options.buffer_size)
        self._set_current(self._verify, options.verify)
        self._set_current(self._conflict, options.conflict)
        self._retry.setValue(options.retry_count)
        self._set_current(self._scan, options.scan_mode)
        self._set_current(self._symlink, options.symlink_policy)
        self._bandwidth.setValue(
            int(options.bandwidth_limit / MIB) if options.bandwidth_limit else 0
        )
        self._hidden.setChecked(options.include_hidden)
        self._system.setChecked(options.include_system)
        self._times.setChecked(options.preserve_times)
        self._permissions.setChecked(options.preserve_permissions)
        self._checkpoint.setChecked(options.use_checkpoint)
        self._partial.setChecked(options.use_partial_suffix)
        self._dry_run.setChecked(options.dry_run)

    def _collect(self) -> TransferOptions:
        workers = self._workers.value()
        bandwidth = self._bandwidth.value()
        return replace(
            self.options,
            workers=workers or None,
            buffer_size=self._buffer.currentData(),
            verify=self._verify.currentData(),
            conflict=self._conflict.currentData(),
            retry_count=self._retry.value(),
            scan_mode=self._scan.currentData(),
            symlink_policy=self._symlink.currentData(),
            bandwidth_limit=bandwidth * MIB if bandwidth else None,
            include_hidden=self._hidden.isChecked(),
            include_system=self._system.isChecked(),
            preserve_times=self._times.isChecked(),
            preserve_permissions=self._permissions.isChecked(),
            use_checkpoint=self._checkpoint.isChecked(),
            use_partial_suffix=self._partial.isChecked(),
            dry_run=self._dry_run.isChecked(),
        )

    def accept(self) -> None:
        self.options = self._collect()
        super().accept()

    def _on_preset_changed(self, _index: int) -> None:
        preset = self._preset.currentData()
        if preset is SpeedPreset.CUSTOM:
            return
        self._load_without_marking(preset_options(preset, self._collect()))

    def _load_without_marking(self, options: TransferOptions) -> None:
        preset_index = self._preset.currentIndex()
        widgets = self.findChildren((QComboBox, QSpinBox, QCheckBox))
        for widget in widgets:
            widget.blockSignals(True)
        self._load(options)
        self._preset.setCurrentIndex(preset_index)
        for widget in widgets:
            widget.blockSignals(False)

    def _mark_custom(self) -> None:
        self._set_current(self._preset, SpeedPreset.CUSTOM)

    def _set_closest_buffer(self, size: int) -> None:
        closest = min(BUFFER_CHOICES, key=lambda choice: abs(choice - size))
        index = self._buffer.findData(closest)
        if index >= 0:
            self._buffer.setCurrentIndex(index)

    @staticmethod
    def _set_current(combo: QComboBox, value: object) -> None:
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)
