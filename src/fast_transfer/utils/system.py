"""Storage introspection and the auto worker-count policy."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from .paths import extended_path, is_network_path, same_volume, volume_root


class StorageKind(StrEnum):
    """How the underlying device behaves under parallel I/O."""

    HDD = "hdd"
    SSD = "ssd"
    NETWORK = "network"
    REMOVABLE = "removable"
    UNKNOWN = "unknown"


@dataclass(slots=True, frozen=True)
class DiskInfo:
    """What we could learn about the volume behind a path."""

    root: str
    kind: StorageKind
    total_bytes: int = 0
    free_bytes: int = 0


def free_space(path: str | os.PathLike[str]) -> int:
    """Free bytes on the volume holding `path` (0 when it cannot be determined)."""
    try:
        return shutil.disk_usage(extended_path(volume_root(path))).free
    except OSError:
        return 0


def has_free_space(path: str | os.PathLike[str], required: int, margin: float = 0.02) -> bool:
    """True when `required` bytes fit, keeping a small safety margin free."""
    if required <= 0:
        return True
    available = free_space(path)
    if available <= 0:
        return True  # Unknown -- do not block the user on a bad reading.
    return available >= required * (1.0 + margin)


def probe_storage(path: str | os.PathLike[str]) -> DiskInfo:
    """Classify the device behind `path`. Falls back to UNKNOWN rather than guessing."""
    root = volume_root(path)
    total = free = 0
    try:
        usage = shutil.disk_usage(extended_path(root))
        total, free = usage.total, usage.free
    except OSError:
        pass

    if is_network_path(path):
        return DiskInfo(root=root, kind=StorageKind.NETWORK, total_bytes=total, free_bytes=free)
    return DiskInfo(root=root, kind=_local_kind(root), total_bytes=total, free_bytes=free)


def _local_kind(root: str) -> StorageKind:
    try:
        import psutil
    except ImportError:
        return StorageKind.UNKNOWN

    try:
        for part in psutil.disk_partitions(all=False):
            if part.mountpoint.rstrip("\\/").lower() != root.rstrip("\\/").lower():
                continue
            opts = part.opts.lower()
            if "removable" in opts or "cdrom" in opts:
                return StorageKind.REMOVABLE
            if "remote" in opts:
                return StorageKind.NETWORK
            return _rotation_kind(root)
    except Exception:
        return StorageKind.UNKNOWN
    return StorageKind.UNKNOWN


def _rotation_kind(root: str) -> StorageKind:
    """Ask Windows whether the drive is seek-penalised (HDD) or not (SSD/NVMe)."""
    if os.name != "nt":
        return StorageKind.UNKNOWN
    try:
        import ctypes
        from ctypes import wintypes

        drive = root.rstrip("\\/")
        if not drive.endswith(":"):
            return StorageKind.UNKNOWN

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.CreateFileW(
            f"\\\\.\\{drive}",
            0,  # query only, no access rights needed
            0x00000001 | 0x00000002,  # FILE_SHARE_READ | FILE_SHARE_WRITE
            None,
            3,  # OPEN_EXISTING
            0,
            None,
        )
        if handle == -1 or handle == ctypes.c_void_p(-1).value:
            return StorageKind.UNKNOWN

        try:

            class _Query(ctypes.Structure):
                _fields_ = [
                    ("PropertyId", wintypes.DWORD),
                    ("QueryType", wintypes.DWORD),
                    ("AdditionalParameters", wintypes.BYTE * 1),
                ]

            class _Descriptor(ctypes.Structure):
                _fields_ = [
                    ("Version", wintypes.DWORD),
                    ("Size", wintypes.DWORD),
                    ("IncursSeekPenalty", wintypes.BOOLEAN),
                ]

            query = _Query()
            query.PropertyId = 7  # StorageDeviceSeekPenaltyProperty
            query.QueryType = 0  # PropertyStandardQuery
            descriptor = _Descriptor()
            returned = wintypes.DWORD()

            ok = kernel32.DeviceIoControl(
                handle,
                0x002D1400,  # IOCTL_STORAGE_QUERY_PROPERTY
                ctypes.byref(query),
                ctypes.sizeof(query),
                ctypes.byref(descriptor),
                ctypes.sizeof(descriptor),
                ctypes.byref(returned),
                None,
            )
            if not ok:
                return StorageKind.UNKNOWN
            return StorageKind.HDD if descriptor.IncursSeekPenalty else StorageKind.SSD
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        return StorageKind.UNKNOWN


# Worker counts from the spec's policy table, keyed by (source kind, destination kind).
_WORKER_MATRIX: dict[tuple[StorageKind, StorageKind], int] = {
    (StorageKind.HDD, StorageKind.HDD): 4,
    (StorageKind.HDD, StorageKind.SSD): 8,
    (StorageKind.SSD, StorageKind.HDD): 6,
    (StorageKind.SSD, StorageKind.SSD): 12,
    (StorageKind.NETWORK, StorageKind.SSD): 12,
    (StorageKind.SSD, StorageKind.NETWORK): 12,
    (StorageKind.NETWORK, StorageKind.NETWORK): 8,
    (StorageKind.NETWORK, StorageKind.HDD): 6,
    (StorageKind.HDD, StorageKind.NETWORK): 6,
    (StorageKind.REMOVABLE, StorageKind.SSD): 4,
    (StorageKind.SSD, StorageKind.REMOVABLE): 4,
}

MIN_WORKERS = 2
MAX_WORKERS = 32


def recommend_workers(
    source: str | os.PathLike[str],
    destination: str | os.PathLike[str],
    average_file_size: int | None = None,
) -> int:
    """Starting worker count for this source/destination pair.

    This is a first guess the user can override, not a measurement. Same-disk
    work is throttled hard because parallel reads on one spindle fight each other.
    """
    src_info = probe_storage(source)
    dst_info = probe_storage(destination)
    same_disk = same_volume(source, destination)

    if same_disk and src_info.kind is StorageKind.HDD:
        workers = 2
    elif same_disk and src_info.kind in (StorageKind.SSD, StorageKind.UNKNOWN):
        workers = 8
    else:
        workers = _WORKER_MATRIX.get((src_info.kind, dst_info.kind), _fallback_workers())

    if average_file_size is not None:
        if average_file_size < 1024 * 1024:  # small-file heavy -> lean on concurrency
            workers = int(workers * 1.5)
        elif average_file_size >= 256 * 1024 * 1024:  # large-file heavy -> back off
            workers = max(2, workers // 2)

    return max(MIN_WORKERS, min(MAX_WORKERS, workers))


def _fallback_workers() -> int:
    cpu = os.cpu_count() or 4
    return min(16, max(4, cpu * 2))


def describe_pair(source: str | os.PathLike[str], destination: str | os.PathLike[str]) -> str:
    """'SSD -> NETWORK (different volumes)' for logs and the GUI status line."""
    src = probe_storage(source).kind.value.upper()
    dst = probe_storage(destination).kind.value.upper()
    relation = "same volume" if same_volume(source, destination) else "different volumes"
    return f"{src} -> {dst} ({relation})"


def average_size(total_bytes: int, total_files: int) -> int | None:
    if total_files <= 0:
        return None
    return total_bytes // total_files


def app_data_dir(app_slug: str) -> Path:
    """%APPDATA%\\<app> for config."""
    base = os.environ.get("APPDATA") or os.path.expanduser("~/.config")
    return Path(base) / app_slug


def local_app_data_dir(app_slug: str) -> Path:
    """%LOCALAPPDATA%\\<app> for logs and checkpoints."""
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~/.local/share")
    return Path(base) / app_slug
