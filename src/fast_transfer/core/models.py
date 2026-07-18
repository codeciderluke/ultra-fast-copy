"""Core data models shared by the CLI and GUI front-ends."""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

KIB = 1024
MIB = 1024 * 1024


class ItemType(StrEnum):
    """Kind of filesystem entry a transfer item refers to."""

    FILE = "file"
    DIRECTORY = "directory"
    SYMLINK = "symlink"


class OperationType(StrEnum):
    """Top level operation requested by the user."""

    COPY = "copy"
    MOVE = "move"


class ConflictPolicy(StrEnum):
    """What to do when the destination already exists."""

    SKIP = "skip"
    OVERWRITE = "overwrite"
    OVERWRITE_IF_NEWER = "overwrite_if_newer"
    OVERWRITE_IF_DIFFERENT = "overwrite_if_different"
    RENAME = "rename"
    ASK = "ask"


class ConflictResolution(StrEnum):
    """Concrete decision produced by the conflict resolver."""

    PROCEED = "proceed"
    SKIP = "skip"
    RENAME = "rename"


class VerifyMode(StrEnum):
    """How a copied file is checked against its source."""

    NONE = "none"
    SIZE = "size"
    MTIME_SIZE = "mtime_size"
    XXHASH = "xxhash"
    SHA256 = "sha256"


class ScanMode(StrEnum):
    """Whether the tree is fully counted before the transfer starts."""

    PRESCAN = "prescan"
    STREAMING = "streaming"


class SymlinkPolicy(StrEnum):
    """How reparse points / symlinks are handled."""

    SKIP = "skip"
    COPY_LINK = "copy_link"
    FOLLOW = "follow"


class JobStatus(StrEnum):
    """Lifecycle state of a transfer job."""

    PENDING = "pending"
    SCANNING = "scanning"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    COMPLETED_WITH_ERRORS = "completed_with_errors"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_STATUSES: frozenset[JobStatus] = frozenset(
    {
        JobStatus.COMPLETED,
        JobStatus.COMPLETED_WITH_ERRORS,
        JobStatus.FAILED,
        JobStatus.CANCELLED,
    }
)


class SpeedPreset(StrEnum):
    """UI level shortcut that fills in a coherent set of options."""

    FAST = "fast"
    BALANCED = "balanced"
    SAFE = "safe"
    CUSTOM = "custom"


@dataclass(slots=True)
class TransferItem:
    """A single unit of work produced by the planner."""

    source: Path
    destination: Path
    size: int
    item_type: ItemType
    relative_path: Path
    modified_time_ns: int | None = None

    @property
    def is_directory(self) -> bool:
        return self.item_type is ItemType.DIRECTORY


@dataclass(slots=True)
class ScanStats:
    """Totals produced by a pre-scan pass."""

    total_files: int = 0
    total_directories: int = 0
    total_bytes: int = 0
    skipped_entries: int = 0


@dataclass(slots=True)
class TransferOptions:
    """Everything that tunes a job. Front-ends build one of these and hand it to the engine."""

    operation: OperationType = OperationType.COPY
    workers: int | None = None  # None -> auto tuned from the paths involved
    buffer_size: int = 4 * MIB
    large_file_buffer_size: int = 8 * MIB
    small_file_threshold: int = 1 * MIB
    large_file_threshold: int = 256 * MIB
    verify: VerifyMode = VerifyMode.SIZE
    conflict: ConflictPolicy = ConflictPolicy.SKIP
    retry_count: int = 3
    retry_base_delay: float = 0.5
    scan_mode: ScanMode = ScanMode.PRESCAN
    symlink_policy: SymlinkPolicy = SymlinkPolicy.SKIP
    include_patterns: tuple[str, ...] = ()
    exclude_patterns: tuple[str, ...] = ()
    include_hidden: bool = True
    include_system: bool = False
    preserve_times: bool = True
    preserve_permissions: bool = False
    use_checkpoint: bool = True
    use_partial_suffix: bool = True
    delete_partial_on_failure: bool = True
    dry_run: bool = False
    bandwidth_limit: int | None = None  # bytes per second, None -> unlimited
    progress_interval: float = 0.2  # seconds between aggregated progress events
    max_large_file_workers: int = 4

    def resolved_verify_for_move(self) -> VerifyMode:
        """Cross-volume moves must never delete the source on an unverified copy."""
        if self.verify is VerifyMode.NONE:
            return VerifyMode.SIZE
        return self.verify

    def buffer_for_size(self, size: int) -> int:
        if size >= self.large_file_threshold:
            return self.large_file_buffer_size
        if size < self.small_file_threshold:
            # Never allocate a buffer larger than the file itself.
            return max(64 * KIB, min(self.buffer_size, size or 64 * KIB))
        return self.buffer_size


def default_workers() -> int:
    cpu = os.cpu_count() or 4
    return min(32, max(4, cpu * 2))


@dataclass(slots=True)
class FileFailure:
    """One file that did not make it, kept so the user can retry just those."""

    source: Path
    destination: Path
    error_code: str
    message: str
    attempts: int = 1

    def as_dict(self) -> dict[str, object]:
        return {
            "source": str(self.source),
            "destination": str(self.destination),
            "error_code": self.error_code,
            "message": self.message,
            "attempts": self.attempts,
        }


@dataclass(slots=True)
class TransferResult:
    """Final summary of a job."""

    job_id: str
    operation: OperationType
    status: JobStatus
    total_files: int = 0
    completed_files: int = 0
    skipped_files: int = 0
    failed_files: int = 0
    total_bytes: int = 0
    completed_bytes: int = 0
    retries: int = 0
    elapsed_seconds: float = 0.0
    failures: list[FileFailure] = field(default_factory=list)

    @property
    def average_speed_bps(self) -> float:
        if self.elapsed_seconds <= 0:
            return 0.0
        return self.completed_bytes / self.elapsed_seconds

    @property
    def succeeded(self) -> bool:
        return self.status is JobStatus.COMPLETED

    def as_dict(self) -> dict[str, object]:
        return {
            "job_id": self.job_id,
            "operation": self.operation.value,
            "status": self.status.value,
            "total_files": self.total_files,
            "completed_files": self.completed_files,
            "skipped_files": self.skipped_files,
            "failed_files": self.failed_files,
            "total_bytes": self.total_bytes,
            "completed_bytes": self.completed_bytes,
            "retries": self.retries,
            "elapsed_seconds": round(self.elapsed_seconds, 3),
            "average_speed_bps": round(self.average_speed_bps, 2),
            "failures": [f.as_dict() for f in self.failures],
        }


@dataclass(slots=True)
class TransferJob:
    """A queued or running job: sources, one destination root, and the options."""

    sources: tuple[Path, ...]
    destination: Path
    options: TransferOptions
    job_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    status: JobStatus = JobStatus.PENDING

    @property
    def operation(self) -> OperationType:
        return self.options.operation
