"""Formatting, errors, control, verifier, checkpoint, and the worker policy."""

from __future__ import annotations

import errno
import threading
from pathlib import Path

import pytest

from fast_transfer.core.checkpoint import (
    CheckpointMeta,
    CheckpointStore,
    options_from_dict,
    options_to_dict,
)
from fast_transfer.core.control import TransferControl
from fast_transfer.core.errors import CancelledError, ErrorCode, classify_os_error
from fast_transfer.core.models import (
    ConflictPolicy,
    JobStatus,
    OperationType,
    ScanMode,
    TransferOptions,
    VerifyMode,
)
from fast_transfer.core.verifier import hash_file, verify, verify_sampled
from fast_transfer.utils.formatting import (
    format_duration,
    format_size,
    format_speed,
    parse_size,
    truncate_middle,
)
from fast_transfer.utils.system import recommend_workers

# -- formatting ------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [(0, "0 B"), (512, "512 B"), (1024, "1.0 KiB"), (1536, "1.5 KiB"), (1048576, "1.0 MiB")],
)
def test_format_size(value: int, expected: str) -> None:
    assert format_size(value) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1024", 1024),
        ("1KiB", 1024),
        ("4MiB", 4 * 1024**2),
        ("8 mib", 8 * 1024**2),
        ("1GB", 1024**3),
        ("1_024", 1024),
        ("2.5MiB", int(2.5 * 1024**2)),
    ],
)
def test_parse_size(text: str, expected: int) -> None:
    assert parse_size(text) == expected


@pytest.mark.parametrize("text", ["abc", "1XB", "", "-5MiB"])
def test_parse_size_rejects_nonsense(text: str) -> None:
    with pytest.raises(ValueError):
        parse_size(text)


def test_parse_size_round_trips_through_the_config() -> None:
    assert parse_size(format_size(4 * 1024**2).replace(" ", "")) == 4 * 1024**2


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [(None, "-"), (-1, "-"), (5, "5s"), (65, "1m 5s"), (3725, "1h 2m 5s")],
)
def test_format_duration(seconds: float | None, expected: str) -> None:
    assert format_duration(seconds) == expected


def test_format_speed() -> None:
    assert format_speed(0) == "-"
    assert format_speed(1024) == "1.0 KiB/s"


def test_truncate_middle_keeps_both_ends() -> None:
    result = truncate_middle("C:\\a\\very\\long\\path\\to\\file.txt", 20)
    assert len(result) <= 20
    assert result.startswith("C:\\a")
    assert result.endswith(".txt")
    assert truncate_middle("short", 20) == "short"


# -- errors ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("err", "code"),
    [
        (errno.EACCES, ErrorCode.ACCESS_DENIED),
        (errno.ENOENT, ErrorCode.FILE_NOT_FOUND),
        (errno.ENOSPC, ErrorCode.DISK_FULL),
        (errno.ENAMETOOLONG, ErrorCode.PATH_TOO_LONG),
        (errno.EEXIST, ErrorCode.DESTINATION_CONFLICT),
    ],
)
def test_classify_errno(err: int, code: ErrorCode) -> None:
    assert classify_os_error(OSError(err, "boom")).code is code


def test_retryable_classification() -> None:
    assert classify_os_error(OSError(errno.EACCES, "locked")).retryable
    assert not classify_os_error(OSError(errno.ENOENT, "gone")).retryable


def test_error_carries_a_user_message() -> None:
    error = classify_os_error(OSError(errno.ENOSPC, "full"), Path("D:/x"))
    assert "space" in error.message.lower()
    assert error.cause is not None


# -- control ---------------------------------------------------------------


def test_control_cancel_raises_at_a_checkpoint() -> None:
    control = TransferControl()
    control.checkpoint()  # no-op while running
    control.cancel()
    assert control.cancelled
    with pytest.raises(CancelledError):
        control.checkpoint()


def test_control_pause_blocks_then_resumes() -> None:
    control = TransferControl()
    control.pause()
    assert control.paused

    released = threading.Event()

    def worker() -> None:
        control.wait_if_paused()
        released.set()

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    assert not released.wait(0.2), "wait_if_paused returned while paused"

    control.resume()
    assert released.wait(2), "wait_if_paused did not return after resume"
    thread.join(2)


def test_cancel_releases_a_paused_worker() -> None:
    """A cancel must not deadlock behind a pause."""
    control = TransferControl()
    control.pause()
    done = threading.Event()

    def worker() -> None:
        try:
            control.checkpoint()
        except CancelledError:
            done.set()

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    control.cancel()
    assert done.wait(2), "cancel did not release the paused worker"
    thread.join(2)


def test_control_reset() -> None:
    control = TransferControl()
    control.cancel()
    control.reset()
    assert not control.cancelled
    assert not control.paused


# -- verifier --------------------------------------------------------------


def test_verify_none_always_passes(tmp_path: Path) -> None:
    a = tmp_path / "a"
    a.write_bytes(b"x")
    assert verify(a, a, VerifyMode.NONE).ok


def test_verify_size(tmp_path: Path) -> None:
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.write_bytes(b"x" * 10)
    b.write_bytes(b"x" * 10)
    assert verify(a, b, VerifyMode.SIZE).ok

    b.write_bytes(b"x" * 11)
    assert not verify(a, b, VerifyMode.SIZE).ok


def test_verify_hash_detects_same_size_corruption(tmp_path: Path) -> None:
    """The case size checks cannot catch."""
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.write_bytes(b"aaaa")
    b.write_bytes(b"aaab")

    assert verify(a, b, VerifyMode.SIZE).ok  # same size: passes
    assert not verify(a, b, VerifyMode.XXHASH).ok
    assert not verify(a, b, VerifyMode.SHA256).ok


def test_hash_is_stable_and_distinct(tmp_path: Path) -> None:
    a = tmp_path / "a"
    a.write_bytes(b"content")
    assert hash_file(a, VerifyMode.XXHASH) == hash_file(a, VerifyMode.XXHASH)
    assert hash_file(a, VerifyMode.XXHASH) != hash_file(a, VerifyMode.SHA256)


def test_verify_missing_destination_fails(tmp_path: Path) -> None:
    a = tmp_path / "a"
    a.write_bytes(b"x")
    assert not verify(a, tmp_path / "missing", VerifyMode.SIZE).ok


def test_sampled_verification(tmp_path: Path) -> None:
    a = tmp_path / "a"
    b = tmp_path / "b"
    payload = bytes(range(256)) * 4000
    a.write_bytes(payload)
    b.write_bytes(payload)
    assert verify_sampled(a, b).ok

    b.write_bytes(payload[:-1] + b"\x00")
    assert not verify_sampled(a, b).ok


# -- worker policy ---------------------------------------------------------


def test_recommend_workers_is_bounded(tmp_path: Path) -> None:
    workers = recommend_workers(tmp_path, tmp_path)
    assert 2 <= workers <= 32


def test_small_files_get_more_workers_than_large_ones(tmp_path: Path) -> None:
    small = recommend_workers(tmp_path, tmp_path, average_file_size=4096)
    large = recommend_workers(tmp_path, tmp_path, average_file_size=512 * 1024**2)
    assert small > large


# -- checkpoint ------------------------------------------------------------


def test_checkpoint_round_trip(tmp_path: Path) -> None:
    store = CheckpointStore("job1", tmp_path / "job1.db")
    store.open()
    try:
        meta = CheckpointMeta(
            job_id="job1",
            operation=OperationType.COPY,
            sources=("C:/src",),
            destination="D:/dst",
            options=options_to_dict(TransferOptions()),
            created_at=1.0,
            updated_at=2.0,
            status=JobStatus.RUNNING,
        )
        store.write_meta(meta)

        loaded = store.read_meta()
        assert loaded is not None
        assert loaded.job_id == "job1"
        assert loaded.operation is OperationType.COPY
        assert loaded.sources == ("C:/src",)
    finally:
        store.close()


def test_checkpoint_tracks_completion(tmp_path: Path) -> None:
    store = CheckpointStore("job2", tmp_path / "job2.db", batch_size=1)
    store.open()
    try:
        source = tmp_path / "a.txt"
        destination = tmp_path / "b.txt"
        source.write_bytes(b"x" * 5)
        destination.write_bytes(b"x" * 5)

        assert not store.is_completed(source)
        store.mark_completed(source, destination, 5)
        assert store.is_completed(source)
        assert store.completed_count() == 1
        assert store.verify_completed(source, destination, 5)

        # A destination whose size no longer matches must be re-transferred.
        assert not store.verify_completed(source, destination, 999)
    finally:
        store.close()


def test_checkpoint_verify_rejects_a_missing_destination(tmp_path: Path) -> None:
    store = CheckpointStore("job3", tmp_path / "job3.db", batch_size=1)
    store.open()
    try:
        source = tmp_path / "a.txt"
        source.write_bytes(b"x")
        store.mark_completed(source, tmp_path / "gone.txt", 1)
        assert not store.verify_completed(source, tmp_path / "gone.txt", 1)
    finally:
        store.close()


def test_checkpoint_records_failures(tmp_path: Path) -> None:
    store = CheckpointStore("job4", tmp_path / "job4.db")
    store.open()
    try:
        store.mark_failed(tmp_path / "a", tmp_path / "b", "access_denied", "denied", 3)
        failures = store.failures()
        assert len(failures) == 1
        assert failures[0]["error_code"] == "access_denied"
        assert failures[0]["attempts"] == 3

        store.clear_failure(tmp_path / "a")
        assert store.failures() == []
    finally:
        store.close()


def test_options_survive_serialisation() -> None:
    original = TransferOptions(
        operation=OperationType.MOVE,
        verify=VerifyMode.XXHASH,
        conflict=ConflictPolicy.RENAME,
        scan_mode=ScanMode.STREAMING,
        exclude_patterns=("*.tmp",),
        workers=7,
    )
    restored = options_from_dict(options_to_dict(original))

    assert restored.operation is OperationType.MOVE
    assert restored.verify is VerifyMode.XXHASH
    assert restored.conflict is ConflictPolicy.RENAME
    assert restored.scan_mode is ScanMode.STREAMING
    assert restored.exclude_patterns == ("*.tmp",)
    assert restored.workers == 7


def test_options_from_dict_ignores_unknown_keys() -> None:
    data = options_to_dict(TransferOptions())
    data["from_a_future_version"] = 1
    assert options_from_dict(data).workers is None


def test_move_never_verifies_with_none() -> None:
    """A cross-volume move deletes the source, so 'no verification' is upgraded."""
    assert TransferOptions(verify=VerifyMode.NONE).resolved_verify_for_move() is VerifyMode.SIZE
    assert TransferOptions(verify=VerifyMode.XXHASH).resolved_verify_for_move() is VerifyMode.XXHASH


def test_buffer_size_scales_with_the_file() -> None:
    options = TransferOptions()
    assert options.buffer_for_size(500 * 1024**2) == options.large_file_buffer_size
    assert options.buffer_for_size(10 * 1024**2) == options.buffer_size
    # Never allocate more than the file itself for tiny files.
    assert options.buffer_for_size(100) <= options.buffer_size
