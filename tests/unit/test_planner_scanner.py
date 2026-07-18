"""Scanner walking and planner path mapping."""

from __future__ import annotations

from pathlib import Path

import pytest

from fast_transfer.core.models import ItemType, TransferOptions
from fast_transfer.core.planner import PatternFilter, TransferPlanner, validate_job
from fast_transfer.core.scanner import Scanner


def test_scanner_finds_everything(source_tree: Path) -> None:
    entries = list(Scanner().scan(source_tree))
    files = [e for e in entries if e.item_type is ItemType.FILE]
    directories = [e for e in entries if e.item_type is ItemType.DIRECTORY]

    assert len(files) == 5
    # source, sub, sub/deep, empty_dir
    assert len(directories) == 4
    assert {f.path.name for f in files} == {
        "top.txt",
        "zero.bin",
        "nested.dat",
        "한글 파일.txt",
        "spaced name.log",
    }


def test_scanner_relative_paths_exclude_the_root(source_tree: Path) -> None:
    """Relative paths must not carry the extended-path prefix or the root name."""
    by_name = {e.path.name: e for e in Scanner().scan(source_tree)}
    assert str(by_name["top.txt"].relative_path) == "top.txt"
    assert Path(str(by_name["nested.dat"].relative_path)) == Path("sub/nested.dat")
    assert "?" not in str(by_name["top.txt"].path)


def test_scanner_reports_a_file_root(tmp_path: Path) -> None:
    single = tmp_path / "only.txt"
    single.write_text("x", encoding="utf-8")
    entries = list(Scanner().scan(single))

    assert len(entries) == 1
    assert entries[0].root_is_file
    assert entries[0].relative_path == Path("only.txt")


def test_scanner_stats(source_tree: Path) -> None:
    scanner = Scanner()
    scanner.measure([source_tree])
    assert scanner.stats.total_files == 5
    assert scanner.stats.total_bytes > 0


def test_scanner_missing_root_reports_an_error(tmp_path: Path) -> None:
    errors = []
    scanner = Scanner(on_error=errors.append)
    assert list(scanner.scan(tmp_path / "nope")) == []
    assert len(errors) == 1


def test_planner_maps_a_folder_under_the_destination(source_tree: Path, tmp_path: Path) -> None:
    destination = tmp_path / "dest"
    planner = TransferPlanner(destination, TransferOptions())
    mapped = {
        e.path.name: planner.plan_entry(e).destination for e in Scanner().scan(source_tree)
    }

    assert mapped["top.txt"] == destination / "source" / "top.txt"
    assert mapped["nested.dat"] == destination / "source" / "sub" / "nested.dat"


def test_planner_maps_a_file_source_directly(tmp_path: Path) -> None:
    single = tmp_path / "only.txt"
    single.write_text("x", encoding="utf-8")
    destination = tmp_path / "dest"

    planner = TransferPlanner(destination, TransferOptions())
    entry = next(iter(Scanner().scan(single)))
    assert planner.plan_entry(entry).destination == destination / "only.txt"


@pytest.mark.parametrize(
    ("pattern", "path", "expected"),
    [
        ("*.txt", "a.txt", True),
        ("*.txt", "a.dat", False),
        ("sub/*", "sub/a.txt", True),
        ("sub/*", "other/a.txt", False),
        ("*.TXT", "a.txt", True),  # Windows globbing is case-insensitive
    ],
)
def test_pattern_filter_matching(pattern: str, path: str, expected: bool) -> None:
    assert PatternFilter(include=[pattern]).allows(Path(path), False) is expected


def test_exclude_beats_include() -> None:
    filter_ = PatternFilter(include=["*.txt"], exclude=["secret*"])
    assert filter_.allows(Path("notes.txt"), False)
    assert not filter_.allows(Path("secret.txt"), False)


def test_include_never_filters_directories() -> None:
    """A directory must be walked even when only files match the include."""
    assert PatternFilter(include=["*.txt"]).allows(Path("sub"), True)


def test_validate_rejects_same_source_and_destination(tmp_path: Path) -> None:
    result = validate_job([tmp_path], tmp_path, TransferOptions())
    assert not result.ok
    assert any("same path" in e for e in result.errors)


def test_validate_rejects_destination_inside_source(tmp_path: Path) -> None:
    source = tmp_path / "source"
    (source / "inner").mkdir(parents=True)
    result = validate_job([source], source / "inner", TransferOptions())
    assert not result.ok
    assert any("inside" in e for e in result.errors)


def test_validate_rejects_a_missing_source(tmp_path: Path) -> None:
    result = validate_job([tmp_path / "gone"], tmp_path / "dest", TransferOptions())
    assert not result.ok


def test_validate_accepts_a_normal_job(source_tree: Path, destination: Path) -> None:
    assert validate_job([source_tree], destination, TransferOptions()).ok


def test_validate_flags_insufficient_space(source_tree: Path, destination: Path) -> None:
    result = validate_job(
        [source_tree], destination, TransferOptions(), required_bytes=10**18
    )
    assert not result.ok
    assert any("free space" in e for e in result.errors)
