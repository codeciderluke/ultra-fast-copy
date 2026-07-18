"""Conflict policy behaviour."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from fast_transfer.core.conflict import ConflictResolver
from fast_transfer.core.models import ConflictPolicy, ConflictResolution


@pytest.fixture
def pair(tmp_path: Path) -> tuple[Path, Path]:
    source = tmp_path / "source.txt"
    destination = tmp_path / "destination.txt"
    source.write_text("source", encoding="utf-8")
    destination.write_text("destination", encoding="utf-8")
    return source, destination


def _age(path: Path, seconds: float) -> None:
    """Shift a file's mtime by `seconds` (negative = older)."""
    st = path.stat()
    os.utime(path, (st.st_atime + seconds, st.st_mtime + seconds))


def test_no_conflict_proceeds(tmp_path: Path) -> None:
    source = tmp_path / "a.txt"
    source.write_text("a", encoding="utf-8")
    decision = ConflictResolver(ConflictPolicy.SKIP).resolve(source, tmp_path / "free.txt")
    assert decision.resolution is ConflictResolution.PROCEED


def test_skip(pair: tuple[Path, Path]) -> None:
    source, destination = pair
    decision = ConflictResolver(ConflictPolicy.SKIP).resolve(source, destination)
    assert decision.resolution is ConflictResolution.SKIP
    assert not decision.should_copy


def test_overwrite(pair: tuple[Path, Path]) -> None:
    source, destination = pair
    decision = ConflictResolver(ConflictPolicy.OVERWRITE).resolve(source, destination)
    assert decision.resolution is ConflictResolution.PROCEED
    assert decision.destination == destination


def test_rename_picks_a_free_name(pair: tuple[Path, Path]) -> None:
    source, destination = pair
    decision = ConflictResolver(ConflictPolicy.RENAME).resolve(source, destination)
    assert decision.resolution is ConflictResolution.RENAME
    assert decision.destination.name == "destination (1).txt"
    assert not decision.destination.exists()


def test_overwrite_if_newer_copies_a_newer_source(pair: tuple[Path, Path]) -> None:
    source, destination = pair
    _age(destination, -600)
    decision = ConflictResolver(ConflictPolicy.OVERWRITE_IF_NEWER).resolve(source, destination)
    assert decision.resolution is ConflictResolution.PROCEED


def test_overwrite_if_newer_skips_an_older_source(pair: tuple[Path, Path]) -> None:
    source, destination = pair
    _age(source, -600)
    decision = ConflictResolver(ConflictPolicy.OVERWRITE_IF_NEWER).resolve(source, destination)
    assert decision.resolution is ConflictResolution.SKIP


def test_overwrite_if_newer_skips_identical_timestamps(pair: tuple[Path, Path]) -> None:
    """Equal mtimes must not re-copy, or every run would rewrite everything."""
    source, destination = pair
    st = source.stat()
    os.utime(destination, ns=(st.st_atime_ns, st.st_mtime_ns))
    decision = ConflictResolver(ConflictPolicy.OVERWRITE_IF_NEWER).resolve(source, destination)
    assert decision.resolution is ConflictResolution.SKIP


def test_overwrite_if_different_uses_size_first(tmp_path: Path) -> None:
    source = tmp_path / "s.txt"
    destination = tmp_path / "d.txt"
    source.write_bytes(b"x" * 100)
    destination.write_bytes(b"x" * 200)
    decision = ConflictResolver(ConflictPolicy.OVERWRITE_IF_DIFFERENT).resolve(source, destination)
    assert decision.resolution is ConflictResolution.PROCEED
    assert "size" in decision.reason.lower()


def test_overwrite_if_different_compares_hashes_on_equal_size(tmp_path: Path) -> None:
    source = tmp_path / "s.txt"
    destination = tmp_path / "d.txt"
    source.write_bytes(b"aaaa")
    destination.write_bytes(b"bbbb")

    from fast_transfer.core.verifier import files_identical

    resolver = ConflictResolver(
        ConflictPolicy.OVERWRITE_IF_DIFFERENT, hash_comparer=files_identical
    )
    assert resolver.resolve(source, destination).resolution is ConflictResolution.PROCEED

    destination.write_bytes(b"aaaa")
    assert resolver.resolve(source, destination).resolution is ConflictResolution.SKIP


def test_ask_without_a_callback_skips(pair: tuple[Path, Path]) -> None:
    """Non-interactive runs must never destroy data by default."""
    source, destination = pair
    decision = ConflictResolver(ConflictPolicy.ASK).resolve(source, destination)
    assert decision.resolution is ConflictResolution.SKIP


def test_ask_uses_the_callback(pair: tuple[Path, Path]) -> None:
    source, destination = pair
    resolver = ConflictResolver(
        ConflictPolicy.ASK, ask_callback=lambda *_: ConflictPolicy.OVERWRITE
    )
    assert resolver.resolve(source, destination).resolution is ConflictResolution.PROCEED


def test_apply_to_all_overrides_the_policy(pair: tuple[Path, Path]) -> None:
    source, destination = pair
    resolver = ConflictResolver(ConflictPolicy.SKIP)
    resolver.set_apply_to_all(ConflictPolicy.OVERWRITE)
    assert resolver.resolve(source, destination).resolution is ConflictResolution.PROCEED
