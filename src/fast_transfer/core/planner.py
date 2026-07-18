"""Turns scan entries into `TransferItem`s and validates the job up front.

Mapping rule, matching Explorer: a source folder is reproduced under the
destination as `destination/<folder>/...`; a source file lands at
`destination/<file>`.
"""

from __future__ import annotations

import fnmatch
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path, PurePath

from ..utils.paths import (
    case_key,
    is_subpath,
    same_path,
    same_volume,
    validate_component,
)
from ..utils.system import free_space, has_free_space
from .errors import ErrorCode, TransferError
from .models import TransferItem, TransferOptions
from .scanner import ScanEntry

_DOT = Path(".")  # the scan root's own relative path


@dataclass(slots=True, frozen=True)
class PlanValidation:
    """Result of the pre-flight checks on a job's roots."""

    ok: bool
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    def raise_if_invalid(self) -> None:
        if not self.ok:
            raise TransferError(ErrorCode.INVALID_PATH, " ".join(self.errors))


class PatternFilter:
    """Glob include/exclude matching against the path relative to its root.

    A pattern without a separator matches the name anywhere; with one, the whole
    relative path. Includes never exclude directories, or the walk could never
    reach a matching file.
    """

    def __init__(
        self, include: Iterable[str] = (), exclude: Iterable[str] = ()
    ) -> None:
        self._include = tuple(p for p in include if p)
        self._exclude = tuple(p for p in exclude if p)

    @property
    def active(self) -> bool:
        return bool(self._include or self._exclude)

    def allows(self, relative: PurePath, is_directory: bool) -> bool:
        text = str(relative).replace("\\", "/")
        name = relative.name

        for pattern in self._exclude:
            if self._matches(pattern, text, name):
                return False

        if not self._include or is_directory:
            return True
        return any(self._matches(pattern, text, name) for pattern in self._include)

    @staticmethod
    def _matches(pattern: str, text: str, name: str) -> bool:
        normalized = pattern.replace("\\", "/")
        if "/" in normalized:
            return fnmatch.fnmatch(text.lower(), normalized.lower())
        return fnmatch.fnmatch(name.lower(), normalized.lower())


class TransferPlanner:
    """Maps scanned entries onto destination paths for a single source root."""

    def __init__(self, destination_root: Path, options: TransferOptions) -> None:
        self._destination_root = Path(destination_root)
        self._options = options
        self._filter = PatternFilter(options.include_patterns, options.exclude_patterns)
        # `destination_root / root.name` per source root, so it is built once
        # rather than once per file.
        self._base_cache: dict[Path, Path] = {}

    @property
    def pattern_filter(self) -> PatternFilter:
        return self._filter

    def destination_for(self, entry: ScanEntry) -> Path:
        """Map a scanned entry onto its destination path.

        A file source lands directly in the destination (`dst/report.pdf`); a
        folder source is reproduced under it (`dst/<folder>/...`), matching what
        Explorer does.
        """
        if entry.root_is_file:
            return self._destination_root / entry.relative_path

        base = self._base_cache.get(entry.root)
        if base is None:
            base = self._destination_root / entry.root.name
            self._base_cache[entry.root] = base

        if entry.relative_path == _DOT:
            return base
        return base / entry.relative_path

    def plan_entry(self, entry: ScanEntry) -> TransferItem | None:
        """One entry -> one item, or None when a filter or a bad name rejects it."""
        if self._filter.active and not self._filter.allows(
            entry.relative_path, entry.is_directory
        ):
            return None

        # Directories validate their whole relative path; a file only needs its
        # own name, since the scanner already yielded (and validated) its
        # parents. relative_to() here would cost more than the copy itself.
        parts = entry.relative_path.parts if entry.is_directory else (entry.relative_path.name,)
        for component in parts:
            if component != "." and validate_component(component) is not None:
                return None

        destination = self.destination_for(entry)

        return TransferItem(
            source=entry.path,
            destination=destination,
            size=entry.size,
            item_type=entry.item_type,
            relative_path=entry.relative_path,
            modified_time_ns=entry.modified_time_ns,
        )

    def plan(self, entries: Iterable[ScanEntry]) -> Iterator[TransferItem]:
        for entry in entries:
            item = self.plan_entry(entry)
            if item is not None:
                yield item

    def is_same_volume_move(self, source: Path) -> bool:
        return same_volume(source, self._destination_root)


def validate_job(
    sources: Iterable[Path],
    destination: Path,
    options: TransferOptions,
    *,
    required_bytes: int | None = None,
) -> PlanValidation:
    """Pre-flight the job: refuse recursion, self-copies, and hopeless paths."""
    errors: list[str] = []
    warnings: list[str] = []
    source_list = [Path(s) for s in sources]
    destination = Path(destination)

    if not source_list:
        errors.append("No source path was given.")

    seen: set[str] = set()
    for source in source_list:
        key = case_key(source)
        if key in seen:
            warnings.append(f"'{source}' was listed more than once and will be transferred once.")
        seen.add(key)

        if not source.exists():
            errors.append(f"The source '{source}' does not exist.")
            continue
        if same_path(source, destination):
            errors.append(f"The source and destination are the same path: '{source}'.")
            continue
        if is_subpath(destination, source):
            errors.append(
                f"The destination '{destination}' is inside the source '{source}', "
                "which would copy the folder into itself."
            )
            continue
        if source.is_file() and same_path(source.parent, destination):
            errors.append(f"'{source}' is already in the destination folder.")

        reason = validate_component(source.name) if source.parent != source else None
        if reason is not None:
            warnings.append(reason)

    if destination.exists() and destination.is_file():
        errors.append(f"The destination '{destination}' is a file, not a folder.")

    if required_bytes and not has_free_space(destination, required_bytes):
        available = free_space(destination)
        errors.append(
            f"Not enough free space at '{destination}': "
            f"{required_bytes:,} bytes needed, {available:,} available."
        )

    if options.dry_run:
        warnings.append("Dry run: no files will be written.")

    return PlanValidation(ok=not errors, errors=tuple(errors), warnings=tuple(warnings))
