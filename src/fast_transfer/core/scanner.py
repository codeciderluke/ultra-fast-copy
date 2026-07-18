"""Recursive `os.scandir` walker.

Yields lazily so streaming mode starts copying at once; the same generator is
drained for pre-scan totals. An unreadable directory is reported and skipped.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

from ..utils.paths import display_path, extended_path, strip_extended
from .control import TransferControl
from .errors import TransferError, classify_os_error
from .models import ItemType, ScanStats, SymlinkPolicy
from .winapi import is_hidden, is_reparse_point, is_system


@dataclass(slots=True)
class ScanEntry:
    """One filesystem entry found by the walk.

    `path` is always a plain path: the \\\\?\\ prefix would corrupt every
    relative-path computation downstream.
    """

    path: Path
    relative_path: Path
    item_type: ItemType
    size: int
    modified_time_ns: int | None
    root: Path
    root_is_file: bool = False

    @property
    def is_directory(self) -> bool:
        return self.item_type is ItemType.DIRECTORY


ScanErrorHandler = Callable[[TransferError], None]
ScanProgressHandler = Callable[[ScanStats, str], None]


class Scanner:
    """Walks one or more roots and yields `ScanEntry` objects."""

    def __init__(
        self,
        *,
        control: TransferControl | None = None,
        symlink_policy: SymlinkPolicy = SymlinkPolicy.SKIP,
        include_hidden: bool = True,
        include_system: bool = False,
        entry_filter: Callable[[Path, Path, bool], bool] | None = None,
        on_error: ScanErrorHandler | None = None,
        on_progress: ScanProgressHandler | None = None,
        progress_every: int = 512,
    ) -> None:
        self._control = control or TransferControl()
        self._symlink_policy = symlink_policy
        self._include_hidden = include_hidden
        self._include_system = include_system
        self._entry_filter = entry_filter
        self._on_error = on_error
        self._on_progress = on_progress
        self._progress_every = max(1, progress_every)
        self.stats = ScanStats()

    def scan(self, root: Path) -> Iterator[ScanEntry]:
        """Walk `root`. A file root yields exactly one entry."""
        root = Path(root)
        try:
            st = os.stat(extended_path(root), follow_symlinks=False)
        except OSError as exc:
            self._report(classify_os_error(exc, root))
            return

        if not os.path.isdir(extended_path(root)):
            entry = ScanEntry(
                path=root,
                relative_path=Path(root.name),
                item_type=ItemType.FILE,
                size=st.st_size,
                modified_time_ns=st.st_mtime_ns,
                root=root.parent,
                root_is_file=True,
            )
            self.stats.total_files += 1
            self.stats.total_bytes += entry.size
            yield entry
            return

        # The root directory itself, so an empty source folder is still created.
        yield ScanEntry(
            path=root,
            relative_path=Path("."),
            item_type=ItemType.DIRECTORY,
            size=0,
            modified_time_ns=st.st_mtime_ns,
            root=root,
        )
        self.stats.total_directories += 1
        # "" is the root's own relative prefix; children extend it by simple
        # concatenation, which is why no entry ever needs relative_to().
        yield from self._walk(root, root, "", visited={self._identity(st)})

    def scan_many(self, roots: list[Path] | tuple[Path, ...]) -> Iterator[ScanEntry]:
        for root in roots:
            self._control.checkpoint()
            yield from self.scan(root)

    def collect(self, roots: list[Path] | tuple[Path, ...]) -> tuple[list[ScanEntry], ScanStats]:
        """Pre-scan: drain the walk into memory for exact totals."""
        entries = list(self.scan_many(roots))
        return entries, self.stats

    def measure(self, roots: list[Path] | tuple[Path, ...]) -> ScanStats:
        """Count files and bytes without retaining the entries.

        This is what pre-scan mode uses on huge trees: exact totals at constant
        memory, at the cost of walking the tree twice.
        """
        for _ in self.scan_many(roots):
            pass
        return self.stats

    def _walk(
        self,
        directory: Path,
        root: Path,
        relative_prefix: str,
        visited: set[tuple[int, int]],
    ) -> Iterator[ScanEntry]:
        """Walk `directory`. `relative_prefix` is its path relative to `root`,
        with a trailing separator (empty at the root)."""
        self._control.checkpoint()
        try:
            iterator = os.scandir(extended_path(directory))
        except OSError as exc:
            self.stats.skipped_entries += 1
            self._report(classify_os_error(exc, directory))
            return

        subdirectories: list[tuple[Path, str]] = []
        with iterator:
            while True:
                self._control.checkpoint()
                try:
                    entry = next(iterator)
                except StopIteration:
                    break
                except OSError as exc:
                    # A single bad entry: log it and stop reading this directory.
                    self.stats.skipped_entries += 1
                    self._report(classify_os_error(exc, directory))
                    break

                produced = self._process(entry, root, relative_prefix)
                if produced is None:
                    continue
                yield produced
                if produced.is_directory:
                    subdirectories.append((produced.path, f"{relative_prefix}{entry.name}\\"))

        for subdirectory, child_prefix in subdirectories:
            child_id = self._identity_of(subdirectory)
            if child_id is not None:
                if child_id in visited:  # symlink/junction loop
                    self.stats.skipped_entries += 1
                    continue
                visited.add(child_id)
            yield from self._walk(subdirectory, root, child_prefix, visited)

    def _process(
        self, entry: os.DirEntry[str], root: Path, relative_prefix: str = ""
    ) -> ScanEntry | None:
        # scandir got an extended path, so entry.path carries the prefix. Strip it
        # here or every path below is offset by the prefix length.
        path = Path(strip_extended(entry.path))
        try:
            st = entry.stat(follow_symlinks=False)
        except OSError as exc:
            self.stats.skipped_entries += 1
            self._report(classify_os_error(exc, path))
            return None

        try:
            is_link = entry.is_symlink() or is_reparse_point(path, st)
        except OSError:
            is_link = False

        if is_link and self._symlink_policy is SymlinkPolicy.SKIP:
            self.stats.skipped_entries += 1
            return None

        try:
            is_dir = entry.is_dir(follow_symlinks=self._symlink_policy is SymlinkPolicy.FOLLOW)
        except OSError as exc:
            self.stats.skipped_entries += 1
            self._report(classify_os_error(exc, path))
            return None

        if not self._include_hidden and is_hidden(path, st):
            self.stats.skipped_entries += 1
            return None
        if not self._include_system and is_system(path, st):
            self.stats.skipped_entries += 1
            return None

        relative_path = Path(relative_prefix + entry.name) if relative_prefix else Path(entry.name)

        if self._entry_filter is not None and not self._entry_filter(path, relative_path, is_dir):
            self.stats.skipped_entries += 1
            return None

        if is_link and self._symlink_policy is SymlinkPolicy.COPY_LINK:
            item_type = ItemType.SYMLINK
            size = 0
        elif is_dir:
            item_type = ItemType.DIRECTORY
            size = 0
        else:
            item_type = ItemType.FILE
            size = st.st_size

        if item_type is ItemType.DIRECTORY:
            self.stats.total_directories += 1
        else:
            self.stats.total_files += 1
            self.stats.total_bytes += size

        self._emit_progress(path)
        return ScanEntry(
            path=path,
            relative_path=relative_path,
            item_type=item_type,
            size=size,
            modified_time_ns=st.st_mtime_ns,
            root=root,
        )

    @staticmethod
    def _identity(st: os.stat_result) -> tuple[int, int]:
        return (st.st_dev, st.st_ino)

    def _identity_of(self, path: Path) -> tuple[int, int] | None:
        try:
            return self._identity(os.stat(extended_path(path)))
        except OSError:
            return None

    def _emit_progress(self, current: Path) -> None:
        if self._on_progress is None:
            return
        total = self.stats.total_files + self.stats.total_directories
        if total % self._progress_every == 0:
            self._on_progress(self.stats, display_path(current.parent))

    def _report(self, error: TransferError) -> None:
        if self._on_error is not None:
            self._on_error(error)
