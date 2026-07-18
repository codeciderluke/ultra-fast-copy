"""Shared fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

from fast_transfer.core.models import TransferOptions, VerifyMode


@pytest.fixture
def source_tree(tmp_path: Path) -> Path:
    """A small tree covering the cases that break naive copiers."""
    root = tmp_path / "source"
    (root / "sub" / "deep").mkdir(parents=True)
    (root / "empty_dir").mkdir()

    (root / "top.txt").write_text("top level", encoding="utf-8")
    (root / "zero.bin").write_bytes(b"")
    (root / "sub" / "nested.dat").write_bytes(b"n" * 5000)
    (root / "sub" / "deep" / "한글 파일.txt").write_text("안녕하세요", encoding="utf-8")
    (root / "sub" / "deep" / "spaced name.log").write_text("log", encoding="utf-8")
    return root


@pytest.fixture
def destination(tmp_path: Path) -> Path:
    target = tmp_path / "destination"
    target.mkdir()
    return target


@pytest.fixture
def options() -> TransferOptions:
    """Deterministic options: no checkpoint side effects, real verification."""
    return TransferOptions(verify=VerifyMode.XXHASH, use_checkpoint=False, workers=4)


def count_files(root: Path) -> int:
    return sum(1 for p in root.rglob("*") if p.is_file())


def total_size(root: Path) -> int:
    return sum(p.stat().st_size for p in root.rglob("*") if p.is_file())


def make_files(directory: Path, count: int, size: int = 100) -> None:
    """Create `count` files of `size` bytes."""
    directory.mkdir(parents=True, exist_ok=True)
    for i in range(count):
        (directory / f"file_{i:05d}.bin").write_bytes(b"x" * size)
