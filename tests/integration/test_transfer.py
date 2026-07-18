"""End-to-end engine behaviour: copy, move, cancel, retry, resume, integrity."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from fast_transfer.core.checkpoint import CheckpointStore
from fast_transfer.core.control import TransferControl
from fast_transfer.core.engine import TransferEngine, transfer
from fast_transfer.core.events import EventEmitter, JobStateEvent, ProgressEvent
from fast_transfer.core.models import (
    ConflictPolicy,
    JobStatus,
    OperationType,
    ScanMode,
    TransferJob,
    TransferOptions,
    VerifyMode,
)
from fast_transfer.core.verifier import hash_file
from tests.conftest import count_files, make_files, total_size

# -- copy ------------------------------------------------------------------


def test_copy_reproduces_the_tree(source_tree: Path, destination: Path, options) -> None:
    result = transfer([source_tree], destination, options)
    copied = destination / "source"

    assert result.status is JobStatus.COMPLETED
    assert result.failed_files == 0
    assert count_files(copied) == count_files(source_tree)
    assert total_size(copied) == total_size(source_tree)


def test_copy_preserves_content_and_hashes(source_tree: Path, destination: Path, options) -> None:
    transfer([source_tree], destination, options)
    copied = destination / "source"

    for original in source_tree.rglob("*"):
        if not original.is_file():
            continue
        mirror = copied / original.relative_to(source_tree)
        assert mirror.exists(), f"{mirror} is missing"
        assert hash_file(original) == hash_file(mirror)


def test_copy_handles_korean_zero_byte_and_empty_dirs(
    source_tree: Path, destination: Path, options
) -> None:
    transfer([source_tree], destination, options)
    copied = destination / "source"

    assert (copied / "sub" / "deep" / "한글 파일.txt").read_text(encoding="utf-8") == "안녕하세요"
    assert (copied / "zero.bin").exists()
    assert (copied / "zero.bin").stat().st_size == 0
    assert (copied / "empty_dir").is_dir()


def test_copy_leaves_no_partial_files(source_tree: Path, destination: Path, options) -> None:
    transfer([source_tree], destination, options)
    assert list(destination.rglob("*.fasttransfer.partial")) == []


def test_copy_preserves_mtime(source_tree: Path, destination: Path) -> None:
    transfer([source_tree], destination, TransferOptions(preserve_times=True, use_checkpoint=False))
    original = source_tree / "top.txt"
    mirror = destination / "source" / "top.txt"
    assert abs(original.stat().st_mtime_ns - mirror.stat().st_mtime_ns) < 2_000_000_000


def test_copy_a_single_file(tmp_path: Path, destination: Path) -> None:
    single = tmp_path / "one.txt"
    single.write_text("hello", encoding="utf-8")
    result = transfer([single], destination, TransferOptions(use_checkpoint=False))

    assert result.status is JobStatus.COMPLETED
    assert (destination / "one.txt").read_text(encoding="utf-8") == "hello"


def test_copy_multiple_sources(tmp_path: Path, destination: Path) -> None:
    """Multi-select: several folders in one job."""
    roots = []
    for name in ("Alpha", "Beta", "Gamma"):
        root = tmp_path / name
        make_files(root, 3)
        roots.append(root)

    result = transfer(roots, destination, TransferOptions(use_checkpoint=False))

    assert result.status is JobStatus.COMPLETED
    assert result.completed_files == 9
    for name in ("Alpha", "Beta", "Gamma"):
        assert count_files(destination / name) == 3


def test_streaming_mode_matches_prescan(source_tree: Path, tmp_path: Path) -> None:
    prescan_target = tmp_path / "prescan"
    streaming_target = tmp_path / "streaming"
    prescan_target.mkdir()
    streaming_target.mkdir()

    a = transfer([source_tree], prescan_target, TransferOptions(use_checkpoint=False, scan_mode=ScanMode.PRESCAN))
    b = transfer([source_tree], streaming_target, TransferOptions(use_checkpoint=False, scan_mode=ScanMode.STREAMING))

    assert a.completed_files == b.completed_files
    assert count_files(prescan_target) == count_files(streaming_target)


def test_dry_run_writes_nothing(source_tree: Path, destination: Path) -> None:
    result = transfer(
        [source_tree], destination, TransferOptions(dry_run=True, use_checkpoint=False)
    )
    assert result.status is JobStatus.COMPLETED
    assert count_files(destination) == 0


def test_exclude_pattern(source_tree: Path, destination: Path) -> None:
    result = transfer(
        [source_tree],
        destination,
        TransferOptions(exclude_patterns=("*.txt",), use_checkpoint=False),
    )
    assert result.completed_files == 3  # zero.bin, nested.dat, spaced name.log
    assert not (destination / "source" / "top.txt").exists()
    assert not (destination / "source" / "sub" / "deep" / "한글 파일.txt").exists()


def test_include_pattern(source_tree: Path, destination: Path) -> None:
    transfer(
        [source_tree],
        destination,
        TransferOptions(include_patterns=("*.txt",), use_checkpoint=False),
    )
    assert (destination / "source" / "top.txt").exists()
    assert not (destination / "source" / "zero.bin").exists()


# -- conflicts -------------------------------------------------------------


def test_skip_leaves_the_destination_untouched(source_tree: Path, destination: Path) -> None:
    transfer([source_tree], destination, TransferOptions(use_checkpoint=False))
    target = destination / "source" / "top.txt"
    target.write_text("modified", encoding="utf-8")

    result = transfer(
        [source_tree], destination, TransferOptions(conflict=ConflictPolicy.SKIP, use_checkpoint=False)
    )
    assert target.read_text(encoding="utf-8") == "modified"
    assert result.skipped_files > 0


def test_overwrite_replaces_the_destination(source_tree: Path, destination: Path) -> None:
    transfer([source_tree], destination, TransferOptions(use_checkpoint=False))
    target = destination / "source" / "top.txt"
    target.write_text("modified", encoding="utf-8")

    transfer(
        [source_tree],
        destination,
        TransferOptions(conflict=ConflictPolicy.OVERWRITE, use_checkpoint=False),
    )
    assert target.read_text(encoding="utf-8") == "top level"


def test_rename_keeps_both(source_tree: Path, destination: Path) -> None:
    transfer([source_tree], destination, TransferOptions(use_checkpoint=False))
    transfer(
        [source_tree],
        destination,
        TransferOptions(conflict=ConflictPolicy.RENAME, use_checkpoint=False),
    )
    assert (destination / "source" / "top.txt").exists()
    assert (destination / "source" / "top (1).txt").exists()


# -- move ------------------------------------------------------------------


def test_same_volume_move(source_tree: Path, destination: Path) -> None:
    original = count_files(source_tree)
    result = transfer(
        [source_tree], destination, TransferOptions(operation=OperationType.MOVE, use_checkpoint=False)
    )

    assert result.status is JobStatus.COMPLETED
    assert count_files(destination / "source") == original
    assert not (source_tree / "top.txt").exists()


def test_move_prunes_empty_source_directories(source_tree: Path, destination: Path) -> None:
    transfer(
        [source_tree], destination, TransferOptions(operation=OperationType.MOVE, use_checkpoint=False)
    )
    assert not source_tree.exists() or count_files(source_tree) == 0


def test_move_keeps_the_source_when_verification_fails(
    tmp_path: Path, destination: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The invariant that matters most: no delete without a verified copy."""
    source = tmp_path / "source"
    source.mkdir()
    victim = source / "important.dat"
    victim.write_bytes(b"irreplaceable")

    from fast_transfer.core import copier as copier_module
    from fast_transfer.core.verifier import VerifyResult

    def always_fail(*_args, **_kwargs):
        return VerifyResult(False, VerifyMode.XXHASH, "forced failure")

    monkeypatch.setattr(copier_module, "verify", always_fail)

    # Force the copy path (not a rename) by pretending the volumes differ.
    from fast_transfer.core import mover as mover_module

    monkeypatch.setattr(mover_module, "same_volume", lambda *_: False)

    result = transfer(
        [source],
        destination,
        TransferOptions(
            operation=OperationType.MOVE, verify=VerifyMode.XXHASH, use_checkpoint=False, retry_count=0
        ),
    )

    assert result.failed_files == 1
    assert victim.exists(), "the source was deleted despite a failed verification"
    assert victim.read_bytes() == b"irreplaceable"
    assert not (destination / "source" / "important.dat").exists()


def test_cross_volume_move_copies_verifies_and_deletes(
    tmp_path: Path, destination: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "a.dat").write_bytes(b"payload")

    from fast_transfer.core import mover as mover_module

    monkeypatch.setattr(mover_module, "same_volume", lambda *_: False)

    result = transfer(
        [source],
        destination,
        TransferOptions(
            operation=OperationType.MOVE, verify=VerifyMode.XXHASH, use_checkpoint=False
        ),
    )

    assert result.status is JobStatus.COMPLETED
    assert (destination / "source" / "a.dat").read_bytes() == b"payload"
    assert not (source / "a.dat").exists()


# -- failure handling ------------------------------------------------------


def test_one_bad_file_does_not_stop_the_job(
    tmp_path: Path, destination: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    make_files(source, 10)

    from fast_transfer.core.copier import FileCopier

    original = FileCopier._stream
    calls = {"n": 0}

    def flaky(self, src: Path, target: Path, size: int, on_bytes):
        calls["n"] += 1
        if src.name == "file_00005.bin":
            raise OSError(13, "Permission denied")
        return original(self, src, target, size, on_bytes)

    monkeypatch.setattr(FileCopier, "_stream", flaky)

    result = transfer(
        [source], destination, TransferOptions(use_checkpoint=False, retry_count=0, workers=2)
    )

    assert result.status is JobStatus.COMPLETED_WITH_ERRORS
    assert result.completed_files == 9
    assert result.failed_files == 1
    assert result.failures[0].source.name == "file_00005.bin"


def test_transient_failures_are_retried(
    tmp_path: Path, destination: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    make_files(source, 1)

    from fast_transfer.core.copier import FileCopier

    original = FileCopier._stream
    attempts = {"n": 0}

    def flaky(self, src: Path, target: Path, size: int, on_bytes):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise OSError(13, "Permission denied")  # retryable
        return original(self, src, target, size, on_bytes)

    monkeypatch.setattr(FileCopier, "_stream", flaky)

    result = transfer(
        [source],
        destination,
        TransferOptions(use_checkpoint=False, retry_count=3, retry_base_delay=0.01, workers=1),
    )

    assert result.status is JobStatus.COMPLETED
    assert result.retries == 2
    assert attempts["n"] == 3


def test_failed_copies_leave_no_partial(
    tmp_path: Path, destination: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    make_files(source, 3)

    from fast_transfer.core.copier import FileCopier

    def always_fail(self, src, target, size, on_bytes):
        target.write_bytes(b"partial junk")  # simulate a half-written file
        raise OSError(5, "I/O error")

    monkeypatch.setattr(FileCopier, "_stream", always_fail)

    result = transfer(
        [source], destination, TransferOptions(use_checkpoint=False, retry_count=0)
    )

    assert result.failed_files == 3
    assert list(destination.rglob("*.partial")) == []


# -- cancel and pause ------------------------------------------------------


def test_cancel_stops_the_job(tmp_path: Path, destination: Path) -> None:
    source = tmp_path / "source"
    make_files(source, 400, size=2048)

    control = TransferControl()
    emitter = EventEmitter()

    def cancel_after_some_progress(event) -> None:
        if isinstance(event, ProgressEvent) and event.completed_files >= 5:
            control.cancel()

    emitter.subscribe(cancel_after_some_progress)

    job = TransferJob(
        sources=(source,),
        destination=destination,
        options=TransferOptions(use_checkpoint=False, workers=2, progress_interval=0.0),
    )
    result = TransferEngine(job, emitter=emitter, control=control).run()

    assert result.status is JobStatus.CANCELLED
    assert count_files(destination) < 400


def test_pause_and_resume(tmp_path: Path, destination: Path) -> None:
    source = tmp_path / "source"
    make_files(source, 200, size=4096)

    job = TransferJob(
        sources=(source,),
        destination=destination,
        options=TransferOptions(use_checkpoint=False, workers=2, progress_interval=0.0),
    )
    engine = TransferEngine(job)
    result: list = []

    thread = threading.Thread(target=lambda: result.append(engine.run()), daemon=True)
    thread.start()

    time.sleep(0.15)
    engine.pause()
    time.sleep(0.1)
    paused_count = count_files(destination)
    time.sleep(0.25)
    # Nothing should move while paused.
    assert count_files(destination) == paused_count

    engine.resume()
    thread.join(timeout=60)

    assert result and result[0].status is JobStatus.COMPLETED
    assert count_files(destination) == 200


# -- checkpoint and resume -------------------------------------------------


def test_checkpoint_lets_a_rerun_skip_finished_files(tmp_path: Path, destination: Path) -> None:
    source = tmp_path / "source"
    make_files(source, 20)

    store = CheckpointStore("resume-test", tmp_path / "cp.db", batch_size=1)
    store.open()
    try:
        job = TransferJob(
            sources=(source,),
            destination=destination,
            options=TransferOptions(use_checkpoint=True, workers=2),
            job_id="resume-test",
        )
        first = TransferEngine(job, checkpoint=store).run()
        assert first.completed_files == 20
    finally:
        store.close()

    # Second pass over the same checkpoint: everything is already done.
    store2 = CheckpointStore("resume-test", tmp_path / "cp.db", batch_size=1)
    store2.open()
    try:
        job2 = TransferJob(
            sources=(source,),
            destination=destination,
            options=TransferOptions(use_checkpoint=True, workers=2),
            job_id="resume-test",
        )
        second = TransferEngine(job2, checkpoint=store2).run()
    finally:
        store2.close()

    assert second.skipped_files == 20
    assert second.completed_files == 0


# -- events ----------------------------------------------------------------


def test_events_report_the_lifecycle(source_tree: Path, destination: Path) -> None:
    emitter = EventEmitter()
    states: list[str] = []
    progress: list[ProgressEvent] = []

    def listen(event) -> None:
        if isinstance(event, JobStateEvent):
            states.append(event.status.value)
        elif isinstance(event, ProgressEvent):
            progress.append(event)

    emitter.subscribe(listen)
    transfer(
        [source_tree],
        destination,
        TransferOptions(use_checkpoint=False, progress_interval=0.0),
        emitter=emitter,
    )

    assert "scanning" in states
    assert "running" in states
    assert states[-1] == "completed"
    assert progress
    assert progress[-1].completed_files == 5


def test_a_broken_listener_cannot_kill_the_job(source_tree: Path, destination: Path) -> None:
    emitter = EventEmitter()
    emitter.subscribe(lambda _event: (_ for _ in ()).throw(RuntimeError("listener bug")))

    result = transfer(
        [source_tree], destination, TransferOptions(use_checkpoint=False), emitter=emitter
    )
    assert result.status is JobStatus.COMPLETED
