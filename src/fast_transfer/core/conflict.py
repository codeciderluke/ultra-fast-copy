"""Conflict policy: decide what happens when the destination already exists."""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ..utils.paths import extended_path, unique_destination
from .models import ConflictPolicy, ConflictResolution

# Filesystem timestamp granularity differs (NTFS 100ns, FAT 2s), so treat
# near-identical mtimes as equal rather than re-copying forever.
MTIME_TOLERANCE_NS = 2_000_000_000

AskCallback = Callable[[Path, Path], ConflictPolicy]


@dataclass(slots=True, frozen=True)
class ConflictDecision:
    """What the engine should do with one conflicting file."""

    resolution: ConflictResolution
    destination: Path
    reason: str = ""

    @property
    def should_copy(self) -> bool:
        return self.resolution is not ConflictResolution.SKIP


class ConflictResolver:
    """Applies a `ConflictPolicy` to a source/destination pair.

    `ask` needs a callback; without one (a non-interactive CLI run) it falls
    back to `skip`, which is the safe default per the spec.
    """

    def __init__(
        self,
        policy: ConflictPolicy = ConflictPolicy.SKIP,
        *,
        ask_callback: AskCallback | None = None,
        hash_comparer: Callable[[Path, Path], bool] | None = None,
    ) -> None:
        self._policy = policy
        self._ask_callback = ask_callback
        self._hash_comparer = hash_comparer
        self._apply_to_all: ConflictPolicy | None = None

    @property
    def policy(self) -> ConflictPolicy:
        return self._policy

    def set_apply_to_all(self, policy: ConflictPolicy) -> None:
        """Remember a user's 'do this for everything' answer."""
        self._apply_to_all = policy

    def resolve(self, source: Path, destination: Path, size: int | None = None) -> ConflictDecision:
        if not _exists(destination):
            return ConflictDecision(ConflictResolution.PROCEED, destination)

        policy = self._apply_to_all or self._policy
        if policy is ConflictPolicy.ASK:
            policy = self._ask(source, destination)

        return self._apply(policy, source, destination, size)

    def _apply(
        self, policy: ConflictPolicy, source: Path, destination: Path, size: int | None
    ) -> ConflictDecision:
        match policy:
            case ConflictPolicy.SKIP:
                return ConflictDecision(
                    ConflictResolution.SKIP, destination, "Destination already exists."
                )
            case ConflictPolicy.OVERWRITE:
                return ConflictDecision(ConflictResolution.PROCEED, destination, "Overwriting.")
            case ConflictPolicy.RENAME:
                return ConflictDecision(
                    ConflictResolution.RENAME,
                    unique_destination(destination),
                    "Renamed to avoid a collision.",
                )
            case ConflictPolicy.OVERWRITE_IF_NEWER:
                return self._if_newer(source, destination)
            case ConflictPolicy.OVERWRITE_IF_DIFFERENT:
                return self._if_different(source, destination, size)
            case _:
                return ConflictDecision(
                    ConflictResolution.SKIP, destination, "Unknown policy; skipped for safety."
                )

    def _if_newer(self, source: Path, destination: Path) -> ConflictDecision:
        src_stat = _stat(source)
        dst_stat = _stat(destination)
        if src_stat is None or dst_stat is None:
            # Cannot compare -> copy, since a missing destination stat means we
            # know less than nothing about it.
            return ConflictDecision(
                ConflictResolution.PROCEED, destination, "Could not compare timestamps."
            )
        if src_stat.st_mtime_ns > dst_stat.st_mtime_ns + MTIME_TOLERANCE_NS:
            return ConflictDecision(ConflictResolution.PROCEED, destination, "Source is newer.")
        return ConflictDecision(
            ConflictResolution.SKIP, destination, "Destination is the same age or newer."
        )

    def _if_different(
        self, source: Path, destination: Path, size: int | None
    ) -> ConflictDecision:
        src_stat = _stat(source)
        dst_stat = _stat(destination)
        if src_stat is None or dst_stat is None:
            return ConflictDecision(
                ConflictResolution.PROCEED, destination, "Could not compare the files."
            )

        source_size = size if size is not None else src_stat.st_size
        if source_size != dst_stat.st_size:
            return ConflictDecision(ConflictResolution.PROCEED, destination, "Sizes differ.")

        if self._hash_comparer is not None:
            if not self._hash_comparer(source, destination):
                return ConflictDecision(ConflictResolution.PROCEED, destination, "Hashes differ.")
            return ConflictDecision(ConflictResolution.SKIP, destination, "Files are identical.")

        if abs(src_stat.st_mtime_ns - dst_stat.st_mtime_ns) > MTIME_TOLERANCE_NS:
            return ConflictDecision(
                ConflictResolution.PROCEED, destination, "Timestamps differ."
            )
        return ConflictDecision(
            ConflictResolution.SKIP, destination, "Same size and timestamp."
        )

    def _ask(self, source: Path, destination: Path) -> ConflictPolicy:
        if self._ask_callback is None:
            return ConflictPolicy.SKIP  # Non-interactive: never destroy data silently.
        answer = self._ask_callback(source, destination)
        return ConflictPolicy.SKIP if answer is ConflictPolicy.ASK else answer


def _exists(path: Path) -> bool:
    try:
        return os.path.lexists(extended_path(path))
    except OSError:
        return False


def _stat(path: Path) -> os.stat_result | None:
    try:
        return os.stat(extended_path(path))
    except OSError:
        return None
