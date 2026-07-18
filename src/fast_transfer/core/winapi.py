"""Thin ctypes layer over the Win32 bits we need.

Everything here degrades to a safe default on non-Windows or when the call
fails, so the rest of the engine never has to branch on platform.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Any

from ..utils.paths import IS_WINDOWS, extended_path

FILE_ATTRIBUTE_READONLY = 0x1
FILE_ATTRIBUTE_HIDDEN = 0x2
FILE_ATTRIBUTE_SYSTEM = 0x4
FILE_ATTRIBUTE_DIRECTORY = 0x10
FILE_ATTRIBUTE_ARCHIVE = 0x20
FILE_ATTRIBUTE_NORMAL = 0x80
FILE_ATTRIBUTE_TEMPORARY = 0x100
FILE_ATTRIBUTE_REPARSE_POINT = 0x400
INVALID_FILE_ATTRIBUTES = 0xFFFFFFFF


def _kernel32() -> Any | None:
    """The kernel32 handle, or None off Windows. ctypes has no useful static type."""
    if not IS_WINDOWS:
        return None
    try:
        import ctypes

        return ctypes.windll.kernel32
    except Exception:
        return None


def get_file_attributes(path: str | os.PathLike[str]) -> int | None:
    """Raw Windows attribute bits, or None when unavailable."""
    kernel32 = _kernel32()
    if kernel32 is None:
        return None
    try:
        attrs = kernel32.GetFileAttributesW(extended_path(path))
    except Exception:
        return None
    if attrs == INVALID_FILE_ATTRIBUTES:
        return None
    return int(attrs)


def set_file_attributes(path: str | os.PathLike[str], attributes: int) -> bool:
    kernel32 = _kernel32()
    if kernel32 is None:
        return False
    try:
        return bool(kernel32.SetFileAttributesW(extended_path(path), attributes))
    except Exception:
        return False


def attributes_from_stat(st: os.stat_result) -> int | None:
    """Windows exposes the attribute bits on stat results; cheaper than a syscall."""
    return getattr(st, "st_file_attributes", None)


def is_hidden(path: str | os.PathLike[str], st: os.stat_result | None = None) -> bool:
    attrs = attributes_from_stat(st) if st is not None else None
    if attrs is None:
        attrs = get_file_attributes(path)
    if attrs is not None:
        return bool(attrs & FILE_ATTRIBUTE_HIDDEN)
    return Path(path).name.startswith(".")  # POSIX convention for the test suite


def is_system(path: str | os.PathLike[str], st: os.stat_result | None = None) -> bool:
    attrs = attributes_from_stat(st) if st is not None else None
    if attrs is None:
        attrs = get_file_attributes(path)
    return bool(attrs & FILE_ATTRIBUTE_SYSTEM) if attrs is not None else False


def is_readonly(path: str | os.PathLike[str], st: os.stat_result | None = None) -> bool:
    attrs = attributes_from_stat(st) if st is not None else None
    if attrs is None:
        attrs = get_file_attributes(path)
    if attrs is not None:
        return bool(attrs & FILE_ATTRIBUTE_READONLY)
    try:
        mode = (st or os.stat(path)).st_mode
    except OSError:
        return False
    return not bool(mode & stat.S_IWUSR)


def is_reparse_point(path: str | os.PathLike[str], st: os.stat_result | None = None) -> bool:
    """Symlink, junction, or other reparse point."""
    attrs = attributes_from_stat(st) if st is not None else None
    if attrs is None:
        attrs = get_file_attributes(path)
    if attrs is not None:
        return bool(attrs & FILE_ATTRIBUTE_REPARSE_POINT)
    return Path(path).is_symlink()


def clear_readonly(path: str | os.PathLike[str]) -> bool:
    """Drop the read-only bit so the file can be overwritten or deleted."""
    attrs = get_file_attributes(path)
    if attrs is None:
        try:
            os.chmod(extended_path(path), stat.S_IWRITE)
            return True
        except OSError:
            return False
    if not attrs & FILE_ATTRIBUTE_READONLY:
        return True
    return set_file_attributes(path, attrs & ~FILE_ATTRIBUTE_READONLY)


_COPYABLE_ATTRIBUTES = (
    FILE_ATTRIBUTE_READONLY | FILE_ATTRIBUTE_HIDDEN | FILE_ATTRIBUTE_SYSTEM
)


def copy_attributes(source: str | os.PathLike[str], destination: str | os.PathLike[str]) -> bool:
    """Carry hidden/system/read-only bits across to the copy.

    A freshly written file is already ARCHIVE and nothing else, which is what
    almost every file is -- so skip the write and save a syscall per file.
    """
    attrs = get_file_attributes(source)
    if attrs is None:
        return False
    interesting = attrs & _COPYABLE_ATTRIBUTES
    if not interesting:
        return True
    return set_file_attributes(destination, interesting | FILE_ATTRIBUTE_ARCHIVE)


def long_paths_enabled() -> bool:
    """Whether the machine has LongPathsEnabled -- informational only.

    The engine prefixes paths with \\\\?\\ regardless, so a False here is not a
    blocker; it just explains why other tools may fail on the same tree.
    """
    if not IS_WINDOWS:
        return True
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\FileSystem"
        ) as key:
            value, _ = winreg.QueryValueEx(key, "LongPathsEnabled")
            return bool(value)
    except Exception:
        return False
