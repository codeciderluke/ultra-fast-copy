"""Error taxonomy: OS exceptions in, classified transfer errors out.

Exceptions are never swallowed; each becomes a `TransferError` carrying a stable
`ErrorCode`, a user-facing message, and the original cause.
"""

from __future__ import annotations

import errno
from enum import StrEnum
from pathlib import Path


class ErrorCode(StrEnum):
    """Stable codes for logs, JSON output, and retry decisions."""

    ACCESS_DENIED = "access_denied"
    FILE_NOT_FOUND = "file_not_found"
    PATH_NOT_FOUND = "path_not_found"
    DISK_FULL = "disk_full"
    FILE_LOCKED = "file_locked"
    SHARING_VIOLATION = "sharing_violation"
    NETWORK_ERROR = "network_error"
    DESTINATION_CONFLICT = "destination_conflict"
    PATH_TOO_LONG = "path_too_long"
    INVALID_PATH = "invalid_path"
    VERIFICATION_FAILED = "verification_failed"
    SOURCE_DELETE_FAILED = "source_delete_failed"
    CHECKPOINT_ERROR = "checkpoint_error"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


# Windows error numbers that are usually transient and worth retrying.
_WINDOWS_SHARING_VIOLATION = 32
_WINDOWS_LOCK_VIOLATION = 33
_WINDOWS_NETWORK_NAME_DELETED = 64
_WINDOWS_NETWORK_BUSY = 54
_WINDOWS_NETWORK_PATH_NOT_FOUND = 53
_WINDOWS_FILENAME_TOO_LONG = 206
_WINDOWS_DISK_FULL = 112

RETRYABLE_CODES: frozenset[ErrorCode] = frozenset(
    {
        ErrorCode.FILE_LOCKED,
        ErrorCode.SHARING_VIOLATION,
        ErrorCode.NETWORK_ERROR,
        ErrorCode.ACCESS_DENIED,  # often transient: AV scanner, indexer, brief lock
    }
)

_USER_MESSAGES: dict[ErrorCode, str] = {
    ErrorCode.ACCESS_DENIED: "Access was denied. The file may be in use or require higher privileges.",
    ErrorCode.FILE_NOT_FOUND: "The source file no longer exists.",
    ErrorCode.PATH_NOT_FOUND: "The path could not be found.",
    ErrorCode.DISK_FULL: "The destination drive is out of free space.",
    ErrorCode.FILE_LOCKED: "The file is locked by another process.",
    ErrorCode.SHARING_VIOLATION: "Another process is using the file.",
    ErrorCode.NETWORK_ERROR: "The network location stopped responding.",
    ErrorCode.DESTINATION_CONFLICT: "A file with the same name already exists at the destination.",
    ErrorCode.PATH_TOO_LONG: "The path is too long for this filesystem.",
    ErrorCode.INVALID_PATH: "The path is not valid on Windows.",
    ErrorCode.VERIFICATION_FAILED: "The copied file did not match the source.",
    ErrorCode.SOURCE_DELETE_FAILED: "The copy succeeded but the original could not be removed.",
    ErrorCode.CHECKPOINT_ERROR: "The resume checkpoint could not be read or written.",
    ErrorCode.CANCELLED: "The operation was cancelled.",
    ErrorCode.UNKNOWN: "An unexpected error occurred.",
}


class TransferError(Exception):
    """A classified failure. `message` is safe to show to a user."""

    def __init__(
        self,
        code: ErrorCode,
        message: str | None = None,
        *,
        path: Path | None = None,
        cause: BaseException | None = None,
    ) -> None:
        self.code = code
        self.path = path
        self.cause = cause
        self.message = message or _USER_MESSAGES[code]
        super().__init__(self.message)

    @property
    def retryable(self) -> bool:
        return self.code in RETRYABLE_CODES

    def __str__(self) -> str:
        if self.path is not None:
            return f"{self.message} ({self.path})"
        return self.message


class CancelledError(TransferError):
    """Raised from control checkpoints when the user cancels."""

    def __init__(self, message: str = "The operation was cancelled.") -> None:
        super().__init__(ErrorCode.CANCELLED, message)


def classify_os_error(exc: OSError, path: Path | None = None) -> TransferError:
    """Map an OSError onto an ErrorCode, preferring the Windows error number."""
    win_err = getattr(exc, "winerror", None)
    code = _classify_winerror(win_err) if win_err is not None else None
    if code is None:
        code = _classify_errno(exc.errno)
    return TransferError(code, path=path or _path_of(exc), cause=exc)


def _path_of(exc: OSError) -> Path | None:
    filename = getattr(exc, "filename", None)
    return Path(filename) if filename else None


def _classify_winerror(win_err: int | None) -> ErrorCode | None:
    match win_err:
        case 2:
            return ErrorCode.FILE_NOT_FOUND
        case 3:
            return ErrorCode.PATH_NOT_FOUND
        case 5:
            return ErrorCode.ACCESS_DENIED
        case _ if win_err == _WINDOWS_SHARING_VIOLATION:
            return ErrorCode.SHARING_VIOLATION
        case _ if win_err == _WINDOWS_LOCK_VIOLATION:
            return ErrorCode.FILE_LOCKED
        case _ if win_err in (
            _WINDOWS_NETWORK_PATH_NOT_FOUND,
            _WINDOWS_NETWORK_BUSY,
            _WINDOWS_NETWORK_NAME_DELETED,
        ):
            return ErrorCode.NETWORK_ERROR
        case _ if win_err == _WINDOWS_DISK_FULL:
            return ErrorCode.DISK_FULL
        case _ if win_err == _WINDOWS_FILENAME_TOO_LONG:
            return ErrorCode.PATH_TOO_LONG
        case _:
            return None


def _classify_errno(err: int | None) -> ErrorCode:
    match err:
        case errno.EACCES | errno.EPERM:
            return ErrorCode.ACCESS_DENIED
        case errno.ENOENT:
            return ErrorCode.FILE_NOT_FOUND
        case errno.ENOSPC | errno.EDQUOT:
            return ErrorCode.DISK_FULL
        case errno.ENAMETOOLONG:
            return ErrorCode.PATH_TOO_LONG
        case errno.EEXIST:
            return ErrorCode.DESTINATION_CONFLICT
        case errno.EINVAL:
            return ErrorCode.INVALID_PATH
        case errno.ENETDOWN | errno.ENETUNREACH | errno.ECONNRESET | errno.EHOSTUNREACH:
            return ErrorCode.NETWORK_ERROR
        case errno.EBUSY | errno.ETXTBSY:
            return ErrorCode.FILE_LOCKED
        case _:
            return ErrorCode.UNKNOWN
