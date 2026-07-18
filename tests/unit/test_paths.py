"""Path handling: long paths, reserved names, volumes, unique names."""

from __future__ import annotations

from pathlib import Path

import pytest

from fast_transfer.utils.paths import (
    IS_WINDOWS,
    case_key,
    display_path,
    extended_path,
    is_reserved_name,
    is_subpath,
    partial_path,
    same_path,
    same_volume,
    sanitize_component,
    strip_extended,
    unique_destination,
    validate_component,
    volume_root,
)


@pytest.mark.parametrize(
    "name",
    ["CON", "con", "PRN", "AUX", "NUL", "COM1", "COM9", "LPT1", "LPT9", "con.txt", "NUL.dat"],
)
def test_reserved_names_are_detected(name: str) -> None:
    assert is_reserved_name(name)


@pytest.mark.parametrize("name", ["CONSOLE", "COM0", "COM10", "LPT0", "report.pdf", "console.txt"])
def test_non_reserved_names_are_allowed(name: str) -> None:
    assert not is_reserved_name(name)


def test_validate_component_rejects_reserved_and_invalid() -> None:
    assert validate_component("normal.txt") is None
    assert validate_component("") is not None
    if IS_WINDOWS:
        assert validate_component("CON") is not None
        assert validate_component("a<b.txt") is not None
        assert validate_component("trailing.") is not None
        assert validate_component("trailing ") is not None


def test_sanitize_component_makes_a_storable_name() -> None:
    assert sanitize_component('a<b>c:d"e|f?g*h') == "a_b_c_d_e_f_g_h"
    assert sanitize_component("trailing. ") == "trailing"
    assert sanitize_component("") == "_"
    if IS_WINDOWS:
        assert sanitize_component("CON") == "_CON"


@pytest.mark.skipif(not IS_WINDOWS, reason="extended paths are a Windows concept")
def test_extended_path_prefixes_local_and_unc() -> None:
    assert extended_path("C:\\temp\\a.txt") == "\\\\?\\C:\\temp\\a.txt"
    assert extended_path("\\\\server\\share\\a.txt") == "\\\\?\\UNC\\server\\share\\a.txt"
    # Already-extended paths must not be double-prefixed.
    assert extended_path("\\\\?\\C:\\temp") == "\\\\?\\C:\\temp"


def test_strip_extended_round_trips() -> None:
    assert strip_extended("\\\\?\\C:\\temp\\a.txt") == "C:\\temp\\a.txt"
    assert strip_extended("\\\\?\\UNC\\server\\share") == "\\\\server\\share"
    assert strip_extended("C:\\temp") == "C:\\temp"


def test_display_path_never_shows_the_prefix() -> None:
    assert "\\\\?\\" not in display_path("\\\\?\\C:\\temp\\a.txt")


@pytest.mark.skipif(not IS_WINDOWS, reason="Windows is case-insensitive")
def test_case_insensitive_comparison() -> None:
    assert same_path("C:\\Temp\\A.txt", "c:\\temp\\a.TXT")
    assert case_key("C:\\Temp") == case_key("c:\\temp")


def test_is_subpath_detects_recursion(tmp_path: Path) -> None:
    parent = tmp_path / "parent"
    child = parent / "child"
    child.mkdir(parents=True)
    assert is_subpath(child, parent)
    assert not is_subpath(parent, child)
    # A path is not a subpath of itself: copying a folder onto itself is a
    # different error than copying it into itself.
    assert not is_subpath(parent, parent)


def test_same_volume_for_same_tree(tmp_path: Path) -> None:
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    assert same_volume(a, b)


def test_same_volume_handles_missing_destination(tmp_path: Path) -> None:
    """The destination often does not exist yet -- walk up to something that does."""
    existing = tmp_path / "here"
    existing.mkdir()
    missing = tmp_path / "not" / "yet" / "created"
    assert same_volume(existing, missing)


def test_volume_root(tmp_path: Path) -> None:
    root = volume_root(tmp_path)
    assert root
    if IS_WINDOWS:
        assert root.endswith("\\")


def test_unique_destination_increments(tmp_path: Path) -> None:
    target = tmp_path / "report.pdf"
    target.write_bytes(b"a")
    first = unique_destination(target)
    assert first.name == "report (1).pdf"

    first.write_bytes(b"b")
    second = unique_destination(target)
    assert second.name == "report (2).pdf"


def test_unique_destination_returns_original_when_free(tmp_path: Path) -> None:
    target = tmp_path / "free.txt"
    assert unique_destination(target) == target


def test_partial_path_naming() -> None:
    assert partial_path(Path("C:/a/example.dat")).name == "example.dat.fasttransfer.partial"


def test_long_path_is_usable(tmp_path: Path) -> None:
    """Paths past MAX_PATH must work through extended_path."""
    import os

    deep = tmp_path
    for i in range(25):
        deep = deep / f"segment_{i:02d}_padding_to_make_this_path_long"
    os.makedirs(extended_path(deep), exist_ok=True)

    target = deep / "file.txt"
    assert len(str(target)) > 260
    with open(extended_path(target), "w", encoding="utf-8") as handle:
        handle.write("deep")
    with open(extended_path(target), encoding="utf-8") as handle:
        assert handle.read() == "deep"
