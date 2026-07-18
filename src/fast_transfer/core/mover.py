"""Move: rename within a volume, copy-verify-delete across volumes.

The invariant that must never break: the source is deleted only after its copy
is verified. If verification fails, the source stays and the failure is reported.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from ..utils.paths import ensure_directory, extended_path, same_volume
from .control import TransferControl
from .copier import CopyOutcome, FileCopier, RateLimiter
from .errors import ErrorCode, TransferError, classify_os_error
from .models import ItemType, TransferItem, TransferOptions
from .winapi import clear_readonly


@dataclass(slots=True)
class MoveOutcome:
    """What one file move did, and how."""

    bytes_moved: int
    strategy: str  # "rename" | "copy_delete"
    source_deleted: bool
    final_path: Path | None = None


class FileMover:
    """Moves one `TransferItem`, picking the strategy from volume identity."""

    def __init__(
        self,
        options: TransferOptions,
        control: TransferControl,
        *,
        rate_limiter: RateLimiter | None = None,
        known_directories: set[str] | None = None,
    ) -> None:
        self._options = options
        self._control = control
        self._known_directories = known_directories
        self._copier = FileCopier(
            options, control, rate_limiter=rate_limiter, known_directories=known_directories
        )

    def move_item(self, item: TransferItem, on_bytes: object | None = None) -> MoveOutcome:
        if item.item_type is ItemType.DIRECTORY:
            self._copier.create_directory(item)
            return MoveOutcome(bytes_moved=0, strategy="rename", source_deleted=False)

        if same_volume(item.source, item.destination.parent):
            return self._rename(item)
        return self._copy_then_delete(item, on_bytes)

    def _rename(self, item: TransferItem) -> MoveOutcome:
        """Same volume: a metadata operation, no data movement at all."""
        if self._options.dry_run:
            return MoveOutcome(item.size, "rename", source_deleted=False, final_path=item.destination)
        try:
            ensure_directory(item.destination.parent)
            if os.path.lexists(extended_path(item.destination)):
                clear_readonly(item.destination)
                os.unlink(extended_path(item.destination))
            os.replace(extended_path(item.source), extended_path(item.destination))
        except OSError as exc:
            error = classify_os_error(exc, item.source)
            # A rename across a mount point inside one drive letter still raises
            # EXDEV; fall back rather than fail the file.
            if getattr(exc, "errno", None) == 18:  # EXDEV
                return self._copy_then_delete(item, None)
            raise error from exc
        return MoveOutcome(
            bytes_moved=item.size, strategy="rename", source_deleted=True, final_path=item.destination
        )

    def _copy_then_delete(self, item: TransferItem, on_bytes: object | None) -> MoveOutcome:
        """Cross volume: copy, verify, and only then remove the source."""
        options = self._options
        verify_mode = options.resolved_verify_for_move()
        copier = self._copier
        if verify_mode is not options.verify:
            # Force a real check for this file without mutating shared options.
            from dataclasses import replace

            copier = FileCopier(
                replace(options, verify=verify_mode),
                self._control,
                rate_limiter=None,
                known_directories=self._known_directories,
            )

        outcome: CopyOutcome = copier.copy_item(item, on_bytes)
        if outcome.verified is not None and not outcome.verified.ok:
            raise TransferError(
                ErrorCode.VERIFICATION_FAILED,
                f"{outcome.verified.detail} The original was kept.",
                path=item.source,
            )

        if options.dry_run:
            return MoveOutcome(outcome.bytes_copied, "copy_delete", source_deleted=False)

        deleted = self._delete_source(item.source)
        return MoveOutcome(
            bytes_moved=outcome.bytes_copied,
            strategy="copy_delete",
            source_deleted=deleted,
            final_path=outcome.final_path,
        )

    def _delete_source(self, source: Path) -> bool:
        try:
            clear_readonly(source)
            os.unlink(extended_path(source))
            return True
        except OSError as exc:
            raise TransferError(
                ErrorCode.SOURCE_DELETE_FAILED,
                "The file was copied and verified, but the original could not be deleted.",
                path=source,
                cause=exc,
            ) from exc


def prune_empty_directories(roots: list[Path] | tuple[Path, ...], control: TransferControl | None = None) -> int:
    """Remove directories left empty by a move, deepest first. Returns the count."""
    removed = 0
    for root in roots:
        if not os.path.isdir(extended_path(root)):
            continue
        for current, dirs, files in os.walk(extended_path(root), topdown=False):
            if control is not None:
                control.checkpoint()
            if files or dirs:
                # `dirs` may list folders we just deleted, so re-check on disk.
                try:
                    if any(os.scandir(current)):
                        continue
                except OSError:
                    continue
            try:
                os.rmdir(current)
                removed += 1
            except OSError:
                continue
    return removed


def can_rename(source: Path, destination: Path) -> bool:
    """Whether a move between these paths would be a same-volume rename."""
    return same_volume(source, destination)
