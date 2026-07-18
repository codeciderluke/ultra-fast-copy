"""Post-copy verification.

`verify()` returns a result rather than raising, so the mover can decide whether
deleting the source is safe.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

from ..utils.paths import extended_path
from .conflict import MTIME_TOLERANCE_NS
from .control import TransferControl
from .errors import ErrorCode, TransferError, classify_os_error
from .models import MIB, VerifyMode

HASH_CHUNK_SIZE = 1 * MIB
SAMPLE_SIZE = 256 * 1024  # bytes read from each of head/middle/tail


@dataclass(slots=True, frozen=True)
class VerifyResult:
    """Outcome of one verification pass."""

    ok: bool
    mode: VerifyMode
    detail: str = ""

    def raise_if_failed(self, path: Path | None = None) -> None:
        if not self.ok:
            raise TransferError(ErrorCode.VERIFICATION_FAILED, self.detail, path=path)


def verify(
    source: Path,
    destination: Path,
    mode: VerifyMode,
    *,
    control: TransferControl | None = None,
    expected_size: int | None = None,
) -> VerifyResult:
    """Compare `destination` against `source` under `mode`."""
    if mode is VerifyMode.NONE:
        return VerifyResult(True, mode, "Verification disabled.")

    try:
        src_stat = os.stat(extended_path(source))
        dst_stat = os.stat(extended_path(destination))
    except OSError as exc:
        error = classify_os_error(exc)
        return VerifyResult(False, mode, f"Could not stat the files: {error.message}")

    source_size = expected_size if expected_size is not None else src_stat.st_size
    if source_size != dst_stat.st_size:
        return VerifyResult(
            False,
            mode,
            f"Size mismatch: source {source_size} bytes, destination {dst_stat.st_size} bytes.",
        )

    match mode:
        case VerifyMode.SIZE:
            return VerifyResult(True, mode, "Sizes match.")
        case VerifyMode.MTIME_SIZE:
            delta = abs(src_stat.st_mtime_ns - dst_stat.st_mtime_ns)
            if delta > MTIME_TOLERANCE_NS:
                return VerifyResult(False, mode, "Modification times differ.")
            return VerifyResult(True, mode, "Size and timestamp match.")
        case VerifyMode.XXHASH | VerifyMode.SHA256:
            return _verify_hash(source, destination, mode, control)
        case _:
            return VerifyResult(True, mode, "Unknown mode; treated as no verification.")


def _verify_hash(
    source: Path, destination: Path, mode: VerifyMode, control: TransferControl | None
) -> VerifyResult:
    try:
        src_digest = hash_file(source, mode, control=control)
        dst_digest = hash_file(destination, mode, control=control)
    except TransferError as exc:
        return VerifyResult(False, mode, f"Could not hash the files: {exc.message}")
    if src_digest != dst_digest:
        return VerifyResult(False, mode, f"{mode.value} mismatch: {src_digest} != {dst_digest}")
    return VerifyResult(True, mode, f"{mode.value} matches ({src_digest}).")


def _new_hasher(mode: VerifyMode) -> object:
    if mode is VerifyMode.SHA256:
        return hashlib.sha256()
    try:
        import xxhash

        return xxhash.xxh3_64()
    except ImportError:
        # Fall back rather than fail: a slower hash still protects the data.
        return hashlib.blake2b(digest_size=8)


def hash_file(
    path: Path,
    mode: VerifyMode = VerifyMode.XXHASH,
    *,
    control: TransferControl | None = None,
    chunk_size: int = HASH_CHUNK_SIZE,
) -> str:
    """Full-file digest. Honours cancel between chunks."""
    hasher = _new_hasher(mode)
    try:
        with open(extended_path(path), "rb", buffering=0) as handle:
            while True:
                if control is not None:
                    control.checkpoint()
                chunk = handle.read(chunk_size)
                if not chunk:
                    break
                hasher.update(chunk)  # type: ignore[attr-defined]
    except OSError as exc:
        raise classify_os_error(exc, path) from exc
    return hasher.hexdigest()  # type: ignore[attr-defined]


def sample_hash(
    path: Path,
    *,
    size: int | None = None,
    sample_size: int = SAMPLE_SIZE,
    mode: VerifyMode = VerifyMode.XXHASH,
) -> str:
    """Digest of the head, middle, and tail only.

    Much cheaper than a full hash on large files but strictly weaker: it cannot
    detect corruption between the sampled regions. Callers must say so in the UI.
    """
    hasher = _new_hasher(mode)
    try:
        file_size = size if size is not None else os.stat(extended_path(path)).st_size
        with open(extended_path(path), "rb", buffering=0) as handle:
            if file_size <= sample_size * 3:
                hasher.update(handle.read())  # type: ignore[attr-defined]
            else:
                for offset in (0, max(0, file_size // 2 - sample_size // 2), file_size - sample_size):
                    handle.seek(offset)
                    hasher.update(handle.read(sample_size))  # type: ignore[attr-defined]
        hasher.update(str(file_size).encode())  # type: ignore[attr-defined]
    except OSError as exc:
        raise classify_os_error(exc, path) from exc
    return hasher.hexdigest()  # type: ignore[attr-defined]


def verify_sampled(
    source: Path, destination: Path, *, expected_size: int | None = None
) -> VerifyResult:
    """Size plus head/middle/tail sample comparison."""
    mode = VerifyMode.XXHASH
    try:
        src_size = os.stat(extended_path(source)).st_size
        dst_size = os.stat(extended_path(destination)).st_size
    except OSError as exc:
        return VerifyResult(False, mode, f"Could not stat the files: {classify_os_error(exc).message}")

    if (expected_size if expected_size is not None else src_size) != dst_size:
        return VerifyResult(False, mode, "Size mismatch.")
    try:
        if sample_hash(source, size=src_size) != sample_hash(destination, size=dst_size):
            return VerifyResult(False, mode, "Sampled regions differ.")
    except TransferError as exc:
        return VerifyResult(False, mode, exc.message)
    return VerifyResult(True, mode, "Sampled verification passed (partial coverage only).")


def files_identical(source: Path, destination: Path, mode: VerifyMode = VerifyMode.XXHASH) -> bool:
    """Hash comparison helper for the `overwrite_if_different` conflict policy."""
    try:
        if os.stat(extended_path(source)).st_size != os.stat(extended_path(destination)).st_size:
            return False
        return hash_file(source, mode) == hash_file(destination, mode)
    except (OSError, TransferError):
        return False
