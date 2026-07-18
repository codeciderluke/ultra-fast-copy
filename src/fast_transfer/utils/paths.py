"""Windows path handling: long paths, UNC, reserved names, volume identity.

Every path reaching the filesystem goes through `extended_path()`, so paths past
MAX_PATH work regardless of the machine's LongPathsEnabled setting.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path, PurePath

IS_WINDOWS = sys.platform == "win32"

EXTENDED_PREFIX = "\\\\?\\"
EXTENDED_UNC_PREFIX = "\\\\?\\UNC\\"
MAX_PATH = 260

# CON, PRN, ... are reserved as *stems*: "con.txt" is just as invalid as "con".
_RESERVED_NAMES: frozenset[str] = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)

# Characters Windows forbids in a filename (the drive colon is handled separately).
_INVALID_CHARS_RE = re.compile(r'[<>:"|?*\x00-\x1f]')


def is_extended(path: str) -> bool:
    return path.startswith(EXTENDED_PREFIX)


def is_unc(path: str) -> bool:
    return path.startswith("\\\\") and not is_extended(path)


def normalize(path: str | os.PathLike[str]) -> Path:
    """Absolute, resolved-as-far-as-possible path without the extended prefix."""
    text = str(path)
    if is_extended(text):
        text = strip_extended(text)
    return Path(os.path.abspath(os.path.expandvars(os.path.expanduser(text))))


def _is_plain_absolute(text: str) -> bool:
    """True for `C:\\dir\\file` -- already absolute and normalized, so `\\\\?\\` can
    be prepended verbatim.

    The extended prefix disables all normalization, so anything Windows would
    have rewritten (forward slashes, `.`/`..`, trailing dots) must not take this
    path and is sent through abspath instead.
    """
    if len(text) < 3 or text[1] != ":" or text[2] != "\\":
        return False
    if "/" in text:
        return False
    return ".." not in text and "\\." not in text


def strip_extended(path: str) -> str:
    """Undo `extended_path()` so a path is presentable to the user."""
    if path.startswith(EXTENDED_UNC_PREFIX):
        return "\\\\" + path[len(EXTENDED_UNC_PREFIX) :]
    if path.startswith(EXTENDED_PREFIX):
        return path[len(EXTENDED_PREFIX) :]
    return path


def extended_path(path: str | os.PathLike[str]) -> str:
    """Return a form of `path` safe to hand to the Windows filesystem APIs.

    On non-Windows this is a no-op, which keeps the engine testable on CI.

    This runs several times per file, and os.path.abspath costs a syscall
    (nt._getfullpathname), so an already-absolute plain path skips it.
    """
    text = str(path)
    if not IS_WINDOWS:
        return text
    if text.startswith(EXTENDED_PREFIX):
        return text
    if _is_plain_absolute(text):
        return EXTENDED_PREFIX + text
    absolute = os.path.abspath(text)
    if absolute.startswith("\\\\"):
        # \\server\share -> \\?\UNC\server\share
        return EXTENDED_UNC_PREFIX + absolute[2:]
    return EXTENDED_PREFIX + absolute


def display_path(path: str | os.PathLike[str]) -> str:
    """Human readable form -- never show the \\\\?\\ prefix in the UI or logs."""
    return strip_extended(str(path))


def is_reserved_name(name: str) -> bool:
    """True for CON, NUL, COM1, and their `name.ext` forms."""
    stem = name.split(".", 1)[0].strip().upper()
    return stem in _RESERVED_NAMES


def has_invalid_characters(name: str) -> bool:
    return bool(_INVALID_CHARS_RE.search(name))


def validate_component(name: str) -> str | None:
    """Return a reason string if `name` cannot exist on Windows, else None."""
    if not name or name in (".", ".."):
        return "Empty or relative path component."
    if IS_WINDOWS:
        if is_reserved_name(name):
            return f"'{name}' uses a reserved Windows device name."
        if has_invalid_characters(name):
            return f"'{name}' contains characters that are invalid on Windows."
        if name.endswith((" ", ".")):
            return f"'{name}' ends with a space or dot, which Windows cannot store."
    return None


def sanitize_component(name: str) -> str:
    """Best-effort rewrite of a component into something Windows can store."""
    cleaned = _INVALID_CHARS_RE.sub("_", name).rstrip(" .")
    if not cleaned:
        cleaned = "_"
    if IS_WINDOWS and is_reserved_name(cleaned):
        cleaned = f"_{cleaned}"
    return cleaned


def case_key(path: str | os.PathLike[str]) -> str:
    """Comparison key honouring Windows' case-insensitive filesystem."""
    text = strip_extended(str(path))
    normalized = os.path.normpath(text)
    return normalized.casefold() if IS_WINDOWS else normalized


def same_path(a: str | os.PathLike[str], b: str | os.PathLike[str]) -> bool:
    return case_key(a) == case_key(b)


def is_subpath(child: str | os.PathLike[str], parent: str | os.PathLike[str]) -> bool:
    """True when `child` is inside `parent`. Used to refuse recursive copies."""
    child_key = case_key(os.path.abspath(strip_extended(str(child))))
    parent_key = case_key(os.path.abspath(strip_extended(str(parent))))
    if child_key == parent_key:
        return False
    return child_key.startswith(parent_key.rstrip(os.sep) + os.sep)


def volume_root(path: str | os.PathLike[str]) -> str:
    """`D:\\` for a local path, `\\\\server\\share` for UNC."""
    text = strip_extended(str(os.path.abspath(str(path))))
    drive, _ = os.path.splitdrive(text)
    if drive:
        return drive + os.sep if len(drive) == 2 and drive[1] == ":" else drive
    return os.sep


def volume_id(path: str | os.PathLike[str]) -> str | None:
    """Volume serial number when we can stat, else the volume root as a fallback.

    Mount points mean two different drive letters can share a volume and one
    drive letter can span volumes, so `st_dev` is the authority when available.
    """
    probe = _nearest_existing(Path(strip_extended(str(path))))
    if probe is not None:
        try:
            return str(os.stat(extended_path(probe)).st_dev)
        except OSError:
            pass
    root = volume_root(path)
    return case_key(root) if root else None


def _nearest_existing(path: Path) -> Path | None:
    """Walk up until something exists -- the destination may not be created yet."""
    current = path.absolute()
    for candidate in (current, *current.parents):
        try:
            if os.path.exists(extended_path(candidate)):
                return candidate
        except OSError:
            continue
    return None


def same_volume(a: str | os.PathLike[str], b: str | os.PathLike[str]) -> bool:
    """Whether a move between these two paths can be a rename instead of a copy."""
    id_a = volume_id(a)
    id_b = volume_id(b)
    if id_a is None or id_b is None:
        return False
    return id_a == id_b


def is_network_path(path: str | os.PathLike[str]) -> bool:
    text = strip_extended(str(os.path.abspath(str(path))))
    if text.startswith("\\\\"):
        return True
    if not IS_WINDOWS:
        return False
    drive, _ = os.path.splitdrive(text)
    if not drive:
        return False
    try:
        import ctypes

        # DRIVE_REMOTE == 4
        return ctypes.windll.kernel32.GetDriveTypeW(f"{drive}\\") == 4
    except Exception:
        return False


def relative_to_root(path: PurePath, root: PurePath) -> Path:
    """`path` relative to `root`, tolerant of case differences on Windows."""
    try:
        return Path(path).relative_to(root)
    except ValueError:
        if IS_WINDOWS:
            path_key = case_key(path)
            root_key = case_key(root).rstrip(os.sep)
            if path_key.startswith(root_key + os.sep):
                return Path(str(path)[len(str(root)) :].lstrip(os.sep))
        raise


def unique_destination(destination: Path, exists: object = None) -> Path:
    """`report.pdf` -> `report (1).pdf` -> `report (2).pdf`, skipping taken names.

    `exists` may be a callable for testing; defaults to a real filesystem check.
    """
    check = exists if callable(exists) else (lambda p: os.path.exists(extended_path(p)))
    if not check(destination):
        return destination
    stem = destination.stem
    suffix = destination.suffix
    parent = destination.parent
    for counter in range(1, 1_000_000):
        candidate = parent / f"{stem} ({counter}){suffix}"
        if not check(candidate):
            return candidate
    raise RuntimeError(f"Could not find a free name for {destination}")


def ensure_directory(path: Path) -> None:
    """mkdir -p that tolerates a concurrent worker winning the race."""
    os.makedirs(extended_path(path), exist_ok=True)


def partial_path(destination: Path) -> Path:
    """Temporary name used while a file is still incomplete."""
    return destination.with_name(destination.name + ".fasttransfer.partial")


def is_partial(path: str | os.PathLike[str]) -> bool:
    return str(path).endswith(".fasttransfer.partial")
