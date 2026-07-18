"""Human readable sizes, rates, durations, and size-string parsing."""

from __future__ import annotations

import re

_UNITS = ("B", "KiB", "MiB", "GiB", "TiB", "PiB")
_SIZE_RE = re.compile(r"^\s*([\d._]+)\s*([a-zA-Z]*)\s*$")
_MULTIPLIERS: dict[str, int] = {
    "": 1,
    "B": 1,
    "K": 1024,
    "KB": 1024,
    "KIB": 1024,
    "M": 1024**2,
    "MB": 1024**2,
    "MIB": 1024**2,
    "G": 1024**3,
    "GB": 1024**3,
    "GIB": 1024**3,
    "T": 1024**4,
    "TB": 1024**4,
    "TIB": 1024**4,
}


def format_size(num_bytes: float) -> str:
    """1536 -> '1.5 KiB'."""
    if num_bytes < 0:
        return "-" + format_size(-num_bytes)
    value = float(num_bytes)
    for unit in _UNITS:
        if value < 1024 or unit == _UNITS[-1]:
            if unit == "B":
                return f"{int(value)} B"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} {_UNITS[-1]}"


def format_speed(bytes_per_second: float) -> str:
    if bytes_per_second <= 0:
        return "-"
    return f"{format_size(bytes_per_second)}/s"


def format_duration(seconds: float | None) -> str:
    """3725 -> '1h 2m 5s'. None or negative -> '-'."""
    if seconds is None or seconds < 0:
        return "-"
    total = int(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes}m {secs}s"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def format_count(value: int) -> str:
    return f"{value:,}"


def parse_size(text: str | int) -> int:
    """'8MiB' -> 8388608. Accepts plain ints and underscores as separators."""
    if isinstance(text, int):
        if text < 0:
            raise ValueError("Size cannot be negative.")
        return text
    match = _SIZE_RE.match(text)
    if not match:
        raise ValueError(f"Cannot parse size: {text!r}")
    number_text, unit = match.groups()
    number_text = number_text.replace("_", "")
    try:
        number = float(number_text)
    except ValueError as exc:
        raise ValueError(f"Cannot parse size: {text!r}") from exc
    multiplier = _MULTIPLIERS.get(unit.upper())
    if multiplier is None:
        raise ValueError(f"Unknown size unit: {unit!r}")
    if number < 0:
        raise ValueError("Size cannot be negative.")
    return int(number * multiplier)


def truncate_middle(text: str, width: int = 60) -> str:
    """Keep the start and end of a path visible: 'D:\\src\\...\\file.txt'."""
    if width <= 3 or len(text) <= width:
        return text
    keep = width - 3
    head = keep // 2
    tail = keep - head
    return f"{text[:head]}...{text[-tail:]}" if tail else f"{text[:head]}..."


def percent(fraction: float | None) -> str:
    if fraction is None:
        return "-"
    return f"{fraction * 100:.1f}%"
