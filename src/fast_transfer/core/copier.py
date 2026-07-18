"""Single-file copy: streaming, metadata-preserving.

Writes to `<name>.fasttransfer.partial` and renames onto the final name only
after the copy and its verification succeed, so an interrupted job can never
leave a truncated file wearing a real filename.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from ..utils.paths import ensure_directory, extended_path, partial_path
from .control import TransferControl
from .errors import ErrorCode, TransferError, classify_os_error
from .models import ItemType, TransferItem, TransferOptions, VerifyMode
from .verifier import VerifyResult, verify
from .winapi import clear_readonly, copy_attributes


@dataclass(slots=True)
class CopyOutcome:
    """What one file copy did."""

    bytes_copied: int
    verified: VerifyResult | None = None
    final_path: Path | None = None


class RateLimiter:
    """Simple token bucket for `--bandwidth-limit`, shared across workers."""

    def __init__(self, bytes_per_second: int | None) -> None:
        self._limit = bytes_per_second
        self._allowance = float(bytes_per_second or 0)
        self._last_check = time.monotonic()
        import threading

        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return bool(self._limit and self._limit > 0)

    def consume(self, amount: int) -> None:
        """Block until `amount` bytes are allowed through."""
        if not self.enabled:
            return
        assert self._limit is not None
        while True:
            with self._lock:
                now = time.monotonic()
                elapsed = now - self._last_check
                self._last_check = now
                self._allowance = min(
                    float(self._limit), self._allowance + elapsed * self._limit
                )
                if self._allowance >= amount:
                    self._allowance -= amount
                    return
                deficit = amount - self._allowance
                wait = deficit / self._limit
            time.sleep(min(wait, 0.25))


ProgressCallback = object  # Callable[[int], None]; kept loose to avoid an import cycle.


class FileCopier:
    """Copies one `TransferItem` at a time. Instances are stateless and thread-safe."""

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
        self._rate_limiter = rate_limiter or RateLimiter(options.bandwidth_limit)
        # Folders the caller has already created. A plain set is safe here: it is
        # only ever read and added to, and a duplicate makedirs is harmless.
        self._known_directories = known_directories

    def copy_item(self, item: TransferItem, on_bytes: ProgressCallback | None = None) -> CopyOutcome:
        """Copy `item`, returning how many bytes moved and the verification result."""
        if item.item_type is ItemType.DIRECTORY:
            self.create_directory(item)
            return CopyOutcome(bytes_copied=0, final_path=item.destination)
        if item.item_type is ItemType.SYMLINK:
            return self._copy_symlink(item)
        return self._copy_file(item, on_bytes)

    def create_directory(self, item: TransferItem) -> None:
        if self._options.dry_run:
            return
        try:
            ensure_directory(item.destination)
        except OSError as exc:
            raise classify_os_error(exc, item.destination) from exc
        if self._options.preserve_times and item.modified_time_ns is not None:
            self._restore_times(item.source, item.destination)

    def _copy_symlink(self, item: TransferItem) -> CopyOutcome:
        if self._options.dry_run:
            return CopyOutcome(bytes_copied=0, final_path=item.destination)
        try:
            target = os.readlink(extended_path(item.source))
            ensure_directory(item.destination.parent)
            if os.path.lexists(extended_path(item.destination)):
                os.unlink(extended_path(item.destination))
            os.symlink(target, extended_path(item.destination), target_is_directory=Path(target).is_dir())
        except OSError as exc:
            raise classify_os_error(exc, item.source) from exc
        return CopyOutcome(bytes_copied=0, final_path=item.destination)

    def _copy_file(self, item: TransferItem, on_bytes: ProgressCallback | None) -> CopyOutcome:
        if self._options.dry_run:
            return CopyOutcome(bytes_copied=item.size, final_path=item.destination)

        self._ensure_parent(item.destination.parent)

        target = (
            partial_path(item.destination)
            if self._options.use_partial_suffix
            else item.destination
        )
        copied = 0
        try:
            copied = self._stream(item.source, target, item.size, on_bytes)
            if self._options.preserve_times:
                self._restore_times(item.source, target, item.modified_time_ns)
            if self._options.preserve_permissions:
                self._restore_permissions(item.source, target)
            copy_attributes(item.source, target)

            result = verify(
                item.source,
                target,
                self._options.verify,
                control=self._control,
                expected_size=item.size,
            )
            if not result.ok:
                self._cleanup(target)
                raise TransferError(
                    ErrorCode.VERIFICATION_FAILED, result.detail, path=item.destination
                )

            final = self._finalize(target, item.destination)
            return CopyOutcome(bytes_copied=copied, verified=result, final_path=final)
        except TransferError:
            if self._options.delete_partial_on_failure:
                self._cleanup(target)
            raise
        except OSError as exc:
            if self._options.delete_partial_on_failure:
                self._cleanup(target)
            raise classify_os_error(exc, item.source) from exc

    def _ensure_parent(self, parent: Path) -> None:
        """Create the destination folder unless it is already known to exist.

        The engine pre-creates every directory, so without this cache each file
        would pay a makedirs syscall to be told what we already know.
        """
        key = str(parent)
        if self._known_directories is not None and key in self._known_directories:
            return
        try:
            ensure_directory(parent)
        except OSError as exc:
            raise classify_os_error(exc, parent) from exc
        if self._known_directories is not None:
            self._known_directories.add(key)

    def _stream(
        self, source: Path, target: Path, size: int, on_bytes: ProgressCallback | None
    ) -> int:
        """Chunked copy honouring cancel, pause, and the bandwidth limit.

        readinto reuses one buffer; read() would allocate a fresh block per
        chunk, which on large files costs more than the copy itself.
        """
        buffer_size = self._options.buffer_for_size(size)
        buffer = bytearray(buffer_size)
        view = memoryview(buffer)
        limiter = self._rate_limiter if self._rate_limiter.enabled else None
        copied = 0
        try:
            with (
                open(extended_path(source), "rb", buffering=0) as reader,
                open(extended_path(target), "wb", buffering=0) as writer,
            ):
                while True:
                    self._control.checkpoint()
                    read = reader.readinto(buffer)
                    if not read:
                        break
                    if limiter is not None:
                        limiter.consume(read)
                    writer.write(view[:read])
                    copied += read
                    if on_bytes is not None:
                        on_bytes(read)  # type: ignore[operator]
        except FileNotFoundError:
            # A stale parent cache entry (the folder was removed underneath us).
            if self._known_directories is not None:
                self._known_directories.discard(str(target.parent))
            raise
        except OSError as exc:
            raise classify_os_error(exc, source) from exc
        return copied

    def _finalize(self, target: Path, destination: Path) -> Path:
        """Atomically move the partial onto the real name."""
        if target == destination:
            return destination
        extended_target = extended_path(target)
        extended_destination = extended_path(destination)
        try:
            # os.replace overwrites an existing destination, so the usual case
            # needs no lexists/unlink pair. Only a read-only file needs help.
            os.replace(extended_target, extended_destination)
        except PermissionError:
            try:
                clear_readonly(destination)
                os.replace(extended_target, extended_destination)
            except OSError as exc:
                raise classify_os_error(exc, destination) from exc
        except OSError as exc:
            raise classify_os_error(exc, destination) from exc
        return destination

    def _cleanup(self, target: Path) -> None:
        """Remove a failed partial. Never raises -- the real error matters more."""
        with contextlib.suppress(OSError):
            if os.path.lexists(extended_path(target)):
                clear_readonly(target)
                os.unlink(extended_path(target))

    @staticmethod
    def _restore_times(source: Path, target: Path, mtime_ns: int | None = None) -> None:
        """Copy the modification time across.

        The scanner already read the mtime, so passing it in avoids a second
        stat per file. Access time is set to match rather than stat'ing for it:
        it carries no information worth a syscall on every file.
        """
        try:
            if mtime_ns is None:
                mtime_ns = os.stat(extended_path(source)).st_mtime_ns
            os.utime(extended_path(target), ns=(mtime_ns, mtime_ns))
        except OSError:
            pass  # Timestamps are best effort; the data is already safe.

    @staticmethod
    def _restore_permissions(source: Path, target: Path) -> None:
        # Best effort: the data is already safe without the mode bits.
        with contextlib.suppress(OSError):
            shutil.copymode(extended_path(source), extended_path(target))


def sweep_partials(root: Path) -> int:
    """Delete leftover `.fasttransfer.partial` files under `root`. Returns the count."""
    removed = 0
    for current, _dirs, files in os.walk(extended_path(root)):
        for name in files:
            if name.endswith(".fasttransfer.partial"):
                try:
                    os.unlink(os.path.join(current, name))
                    removed += 1
                except OSError:
                    continue
    return removed


def copy_metadata_only(source: Path, destination: Path, options: TransferOptions) -> None:
    """Re-apply timestamps/attributes to an already-present destination."""
    if options.preserve_times:
        FileCopier._restore_times(source, destination)
    if options.preserve_permissions:
        FileCopier._restore_permissions(source, destination)
    copy_attributes(source, destination)


def default_verify_for(operation_is_move: bool, cross_volume: bool) -> VerifyMode:
    """Cross-volume moves default to a real hash because the source gets deleted."""
    if operation_is_move and cross_volume:
        return VerifyMode.XXHASH
    return VerifyMode.SIZE
