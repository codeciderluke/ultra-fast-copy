"""Orchestrator: scan -> plan -> bounded queue -> fixed worker pool.

One producer thread walks the tree; a bounded queue applies back-pressure; a
fixed pool copies. A single file's failure never stops the job.
"""

from __future__ import annotations

import queue
import threading
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

from ..utils.formatting import format_size
from ..utils.logging import get_logger
from ..utils.paths import display_path
from ..utils.system import average_size, describe_pair, recommend_workers
from .checkpoint import CheckpointMeta, CheckpointStore, options_to_dict
from .conflict import ConflictResolver
from .control import TransferControl
from .copier import FileCopier, RateLimiter
from .errors import CancelledError, TransferError
from .events import (
    EventEmitter,
    FileEvent,
    JobStateEvent,
    LogEvent,
    ProgressAggregator,
    ScanProgressEvent,
)
from .models import (
    ConflictPolicy,
    ConflictResolution,
    FileFailure,
    ItemType,
    JobStatus,
    OperationType,
    ScanMode,
    ScanStats,
    TransferItem,
    TransferJob,
    TransferOptions,
    TransferResult,
)
from .mover import FileMover, prune_empty_directories
from .planner import TransferPlanner, validate_job
from .scanner import Scanner
from .verifier import files_identical

QUEUE_MULTIPLIER = 8  # queue depth per worker
_SENTINEL = None


class TransferEngine:
    """Runs one `TransferJob` to completion. Create one engine per job."""

    def __init__(
        self,
        job: TransferJob,
        *,
        emitter: EventEmitter | None = None,
        control: TransferControl | None = None,
        checkpoint: CheckpointStore | None = None,
        ask_callback: object | None = None,
    ) -> None:
        self.job = job
        self.options = job.options
        self.emitter = emitter or EventEmitter()
        self.control = control or TransferControl()
        self._logger = get_logger(job.job_id)
        self._checkpoint = checkpoint
        self._owns_checkpoint = checkpoint is None
        self._ask_callback = ask_callback

        self._failures: list[FileFailure] = []
        self._failures_lock = threading.Lock()
        self._retries = 0
        self._status = JobStatus.PENDING
        self._workers = 0
        self._rate_limiter = RateLimiter(self.options.bandwidth_limit)
        self._large_file_gate = threading.BoundedSemaphore(
            max(1, self.options.max_large_file_workers)
        )
        self._directory_lock = threading.Lock()
        self._created_directories: set[str] = set()
        self._progress = ProgressAggregator(
            job_id=job.job_id, emitter=self.emitter, interval=self.options.progress_interval
        )
        self._scan_stats = ScanStats()

    # -- public API --------------------------------------------------------

    @property
    def status(self) -> JobStatus:
        return self._status

    @property
    def worker_count(self) -> int:
        return self._workers

    def pause(self) -> None:
        self.control.pause()
        self._set_status(JobStatus.PAUSED)
        self._log("INFO", "Job paused.")

    def resume(self) -> None:
        self.control.resume()
        self._set_status(JobStatus.RUNNING)
        self._log("INFO", "Job resumed.")

    def cancel(self) -> None:
        self.control.cancel()
        self._log("WARNING", "Cancellation requested.")

    def run(self) -> TransferResult:
        """Execute the job. Returns a result rather than raising for file-level errors."""
        started = time.monotonic()
        self._set_status(JobStatus.SCANNING)
        self._log(
            "INFO",
            f"{self.options.operation.value} {len(self.job.sources)} source(s) -> "
            f"{display_path(self.job.destination)} [{describe_pair(self.job.sources[0], self.job.destination)}]",
        )

        try:
            validation = validate_job(self.job.sources, self.job.destination, self.options)
            for warning in validation.warnings:
                self._log("WARNING", warning)
            validation.raise_if_invalid()

            if self.options.scan_mode is ScanMode.PRESCAN:
                self._prescan()

            self._open_checkpoint()
            self._set_status(JobStatus.RUNNING)
            self._transfer()

            if self.options.operation is OperationType.MOVE and not self.control.cancelled:
                self._prune_sources()

            elapsed = time.monotonic() - started
            result = self._build_result(elapsed)
            self._finish(result)
            return result
        except CancelledError:
            elapsed = time.monotonic() - started
            result = self._build_result(elapsed, forced_status=JobStatus.CANCELLED)
            self._finish(result)
            return result
        except TransferError as exc:
            self._log("ERROR", str(exc))
            elapsed = time.monotonic() - started
            result = self._build_result(elapsed, forced_status=JobStatus.FAILED)
            self._finish(result)
            return result
        finally:
            self._close_checkpoint()

    # -- scanning ----------------------------------------------------------

    def _prescan(self) -> None:
        """Count files and bytes so the progress bar is exact from the first byte."""
        self._log("INFO", "Pre-scanning the source tree...")
        scanner = self._build_scanner(
            on_progress=lambda stats, current: self.emitter.emit(
                ScanProgressEvent(
                    job_id=self.job.job_id,
                    scanned_files=stats.total_files,
                    scanned_directories=stats.total_directories,
                    scanned_bytes=stats.total_bytes,
                    current_directory=current,
                )
            )
        )
        stats = scanner.measure(list(self.job.sources))
        self._scan_stats = stats
        self._progress.set_totals(stats.total_files, stats.total_bytes)
        self.emitter.emit(
            ScanProgressEvent(
                job_id=self.job.job_id,
                scanned_files=stats.total_files,
                scanned_directories=stats.total_directories,
                scanned_bytes=stats.total_bytes,
                done=True,
            )
        )
        self._log(
            "INFO",
            f"Pre-scan found {stats.total_files:,} files, {stats.total_directories:,} folders, "
            f"{format_size(stats.total_bytes)}.",
        )

    def _build_scanner(self, on_progress: object | None = None) -> Scanner:
        return Scanner(
            control=self.control,
            symlink_policy=self.options.symlink_policy,
            include_hidden=self.options.include_hidden,
            include_system=self.options.include_system,
            on_error=lambda error: self._record_scan_error(error),
            on_progress=on_progress,  # type: ignore[arg-type]
        )

    def _record_scan_error(self, error: TransferError) -> None:
        self._log("WARNING", f"Skipped during scan: {error}")

    # -- transfer ----------------------------------------------------------

    def _decide_workers(self) -> int:
        if self.options.workers is not None and self.options.workers > 0:
            return self.options.workers
        mean = average_size(self._scan_stats.total_bytes, self._scan_stats.total_files)
        return recommend_workers(self.job.sources[0], self.job.destination, mean)

    def _transfer(self) -> None:
        self._workers = self._decide_workers()
        self._log("INFO", f"Using {self._workers} worker thread(s).")

        work_queue: queue.Queue[TransferItem | None] = queue.Queue(
            maxsize=self._workers * QUEUE_MULTIPLIER
        )
        producer_error: list[BaseException] = []

        producer = threading.Thread(
            target=self._produce,
            args=(work_queue, producer_error),
            name=f"ufc-scan-{self.job.job_id}",
            daemon=True,
        )
        producer.start()

        with ThreadPoolExecutor(
            max_workers=self._workers, thread_name_prefix=f"ufc-{self.job.job_id}"
        ) as pool:
            futures = [pool.submit(self._consume, work_queue) for _ in range(self._workers)]
            producer.join()
            self._signal_completion(work_queue)
            for future in futures:
                future.result()

        self._progress.flush()
        if producer_error and not isinstance(producer_error[0], CancelledError):
            raise producer_error[0]
        if self.control.cancelled:
            raise CancelledError()

    def _produce(self, work_queue: queue.Queue[TransferItem | None], errors: list[BaseException]) -> None:
        """Walk, plan, pre-create directories, and feed the queue."""
        try:
            for item in self._plan_items():
                self.control.checkpoint()
                if item.item_type is ItemType.DIRECTORY:
                    self._ensure_directory(item)
                    continue
                if self.options.scan_mode is ScanMode.STREAMING:
                    self._progress.add_totals(1, item.size)
                while True:
                    try:
                        work_queue.put(item, timeout=0.2)
                        break
                    except queue.Full:
                        self.control.checkpoint()
        except BaseException as exc:
            errors.append(exc)

    def _signal_completion(self, work_queue: queue.Queue[TransferItem | None]) -> None:
        """Send one sentinel per worker.

        A blocking put would deadlock after a cancel: the workers have already
        exited, so nothing drains the full queue. Drain it instead and retry.
        """
        for _ in range(self._workers):
            while True:
                try:
                    work_queue.put(_SENTINEL, timeout=0.2)
                    break
                except queue.Full:
                    self._drain(work_queue)

    @staticmethod
    def _drain(work_queue: queue.Queue[TransferItem | None]) -> None:
        while True:
            try:
                work_queue.get_nowait()
                work_queue.task_done()
            except queue.Empty:
                return

    def _plan_items(self) -> Iterator[TransferItem]:
        scanner = self._build_scanner(
            on_progress=(
                lambda stats, current: self.emitter.emit(
                    ScanProgressEvent(
                        job_id=self.job.job_id,
                        scanned_files=stats.total_files,
                        scanned_directories=stats.total_directories,
                        scanned_bytes=stats.total_bytes,
                        current_directory=current,
                    )
                )
                if self.options.scan_mode is ScanMode.STREAMING
                else None
            )
        )
        planner = TransferPlanner(self.job.destination, self.options)
        for source in self.job.sources:
            for entry in scanner.scan(Path(source)):
                item = planner.plan_entry(entry)
                if item is not None:
                    yield item

    def _ensure_directory(self, item: TransferItem) -> None:
        """Create a destination folder once.

        The key must be the plain string the copier will look up, since this set
        is what lets each file skip its own makedirs.
        """
        key = str(item.destination)
        with self._directory_lock:
            if key in self._created_directories:
                return
        try:
            FileCopier(self.options, self.control).create_directory(item)
        except TransferError as exc:
            self._record_failure(item, exc, attempts=1)
            return
        with self._directory_lock:
            self._created_directories.add(key)

    def _consume(self, work_queue: queue.Queue[TransferItem | None]) -> None:
        # The producer has already created these, so the copier can skip a
        # makedirs syscall per file.
        copier = FileCopier(
            self.options,
            self.control,
            rate_limiter=self._rate_limiter,
            known_directories=self._created_directories,
        )
        mover = FileMover(
            self.options,
            self.control,
            rate_limiter=self._rate_limiter,
            known_directories=self._created_directories,
        )
        resolver = ConflictResolver(
            self.options.conflict,
            ask_callback=self._ask_callback,  # type: ignore[arg-type]
            hash_comparer=files_identical
            if self.options.conflict is ConflictPolicy.OVERWRITE_IF_DIFFERENT
            else None,
        )
        while True:
            try:
                item = work_queue.get(timeout=0.2)
            except queue.Empty:
                if self.control.cancelled:
                    return
                continue
            if item is _SENTINEL:
                return
            try:
                if self.control.cancelled:
                    return
                self._handle_item(item, copier, mover, resolver)
            except CancelledError:
                return  # cancellation is reported by run(), not per worker
            finally:
                work_queue.task_done()

    def _handle_item(
        self,
        item: TransferItem,
        copier: FileCopier,
        mover: FileMover,
        resolver: ConflictResolver,
    ) -> None:
        self.control.checkpoint()

        if self._checkpoint is not None and self._checkpoint.verify_completed(
            item.source, item.destination, item.size
        ):
            self._progress.file_completed(item.size, count_bytes=True)
            self._emit_file(item, "skipped", message="Already transferred (resumed).")
            self._progress.file_skipped()
            return

        decision = resolver.resolve(item.source, item.destination, item.size)
        if decision.resolution is ConflictResolution.SKIP:
            self._progress.file_skipped()
            self._emit_file(item, "skipped", message=decision.reason)
            return
        if decision.destination != item.destination:
            item = replace(item, destination=decision.destination)

        self._progress.file_started(item.source)
        self._run_with_retry(item, copier, mover)

    def _run_with_retry(self, item: TransferItem, copier: FileCopier, mover: FileMover) -> None:
        """Transfer one file, retrying transient failures with exponential backoff."""
        attempts = 0
        max_attempts = max(1, self.options.retry_count + 1)
        large = item.size >= self.options.large_file_threshold

        while True:
            attempts += 1
            gate_taken = False
            try:
                if large:
                    self._large_file_gate.acquire()
                    gate_taken = True
                self._execute(item, copier, mover)
                self._on_success(item)
                return
            except CancelledError:
                raise
            except TransferError as exc:
                if exc.retryable and attempts < max_attempts:
                    self._retries += 1
                    delay = self.options.retry_base_delay * (2 ** (attempts - 1))
                    self._log(
                        "WARNING",
                        f"Retry {attempts}/{max_attempts - 1} in {delay:.1f}s for "
                        f"{display_path(item.source)}: {exc.message}",
                    )
                    self._sleep_interruptibly(delay)
                    continue
                self._record_failure(item, exc, attempts)
                return
            finally:
                if gate_taken:
                    self._large_file_gate.release()

    def _execute(self, item: TransferItem, copier: FileCopier, mover: FileMover) -> None:
        on_bytes = self._progress.bytes_advanced
        if self.options.operation is OperationType.MOVE:
            outcome = mover.move_item(item, on_bytes)
            if outcome.strategy == "rename":
                # No bytes streamed, but the file did move -- count it so the
                # progress bar reflects reality on same-volume moves.
                self._progress.bytes_advanced(item.size)
        else:
            copier.copy_item(item, on_bytes)

    def _on_success(self, item: TransferItem) -> None:
        self._progress.file_completed(item.size)
        if self._checkpoint is not None:
            self._checkpoint.mark_completed(
                item.source, item.destination, item.size, item.modified_time_ns
            )
        self._emit_file(item, "completed")

    def _record_failure(self, item: TransferItem, error: TransferError, attempts: int) -> None:
        failure = FileFailure(
            source=item.source,
            destination=item.destination,
            error_code=error.code.value,
            message=error.message,
            attempts=attempts,
        )
        with self._failures_lock:
            self._failures.append(failure)
        self._progress.file_failed()
        if self._checkpoint is not None:
            self._checkpoint.mark_failed(
                item.source, item.destination, error.code.value, error.message, attempts
            )
        self._log("ERROR", f"{display_path(item.source)}: {error.message}")
        self._emit_file(
            item, "failed", error_code=error.code.value, message=error.message, attempts=attempts
        )

    def _sleep_interruptibly(self, seconds: float) -> None:
        """Back off without ignoring a cancel that arrives mid-wait."""
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.control.checkpoint()
            time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))

    def _prune_sources(self) -> None:
        removed = prune_empty_directories(self.job.sources, self.control)
        if removed:
            self._log("INFO", f"Removed {removed:,} empty source folder(s).")

    # -- checkpoint --------------------------------------------------------

    def _open_checkpoint(self) -> None:
        if not self.options.use_checkpoint or self.options.dry_run:
            self._checkpoint = None
            return
        if self._checkpoint is None:
            self._checkpoint = CheckpointStore(self.job.job_id)
            self._checkpoint.open()
            self._owns_checkpoint = True
        now = time.time()
        self._checkpoint.write_meta(
            CheckpointMeta(
                job_id=self.job.job_id,
                operation=self.options.operation,
                sources=tuple(str(s) for s in self.job.sources),
                destination=str(self.job.destination),
                options=options_to_dict(self.options),
                created_at=now,
                updated_at=now,
                status=JobStatus.RUNNING,
                total_files=self._scan_stats.total_files,
                total_bytes=self._scan_stats.total_bytes,
            )
        )

    def _close_checkpoint(self) -> None:
        if self._checkpoint is None:
            return
        try:
            snapshot = self._progress.snapshot()
            self._checkpoint.update_progress(
                self._status, snapshot.completed_files, snapshot.completed_bytes
            )
            if self._status is JobStatus.COMPLETED:
                self._checkpoint.delete()  # Nothing left to resume.
            else:
                self._checkpoint.close()
        except TransferError as exc:
            self._log("WARNING", f"Checkpoint could not be finalised: {exc.message}")

    # -- results and events ------------------------------------------------

    def _build_result(
        self, elapsed: float, forced_status: JobStatus | None = None
    ) -> TransferResult:
        snapshot = self._progress.snapshot()
        with self._failures_lock:
            failures = list(self._failures)

        if forced_status is not None:
            status = forced_status
        elif failures:
            status = JobStatus.COMPLETED_WITH_ERRORS
        else:
            status = JobStatus.COMPLETED

        return TransferResult(
            job_id=self.job.job_id,
            operation=self.options.operation,
            status=status,
            total_files=snapshot.total_files or snapshot.completed_files,
            completed_files=snapshot.completed_files,
            skipped_files=snapshot.skipped_files,
            failed_files=len(failures),
            total_bytes=snapshot.total_bytes or snapshot.completed_bytes,
            completed_bytes=snapshot.completed_bytes,
            retries=self._retries,
            elapsed_seconds=elapsed,
            failures=failures,
        )

    def _finish(self, result: TransferResult) -> None:
        self._status = result.status
        self._progress.flush()
        self._log(
            "INFO",
            f"{result.status.value}: {result.completed_files:,} file(s), "
            f"{format_size(result.completed_bytes)} in {result.elapsed_seconds:.1f}s "
            f"({format_size(result.average_speed_bps)}/s), "
            f"{result.failed_files:,} failed, {result.skipped_files:,} skipped.",
        )
        self.emitter.emit(JobStateEvent(self.job.job_id, result.status, result))

    def _set_status(self, status: JobStatus) -> None:
        self._status = status
        self.job.status = status
        self.emitter.emit(JobStateEvent(self.job.job_id, status))

    def _emit_file(
        self,
        item: TransferItem,
        outcome: str,
        *,
        error_code: str | None = None,
        message: str | None = None,
        attempts: int = 1,
    ) -> None:
        self.emitter.emit(
            FileEvent(
                job_id=self.job.job_id,
                source=item.source,
                destination=item.destination,
                size=item.size,
                outcome=outcome,
                error_code=error_code,
                message=message,
                attempts=attempts,
            )
        )

    def _log(self, level: str, message: str) -> None:
        getattr(self._logger, level.lower(), self._logger.info)(message)
        self.emitter.emit(LogEvent(self.job.job_id, level, message))


def transfer(
    sources: list[Path] | tuple[Path, ...],
    destination: Path,
    options: TransferOptions | None = None,
    *,
    emitter: EventEmitter | None = None,
    control: TransferControl | None = None,
) -> TransferResult:
    """Convenience entry point for a one-shot transfer."""
    job = TransferJob(
        sources=tuple(Path(s) for s in sources),
        destination=Path(destination),
        options=options or TransferOptions(),
    )
    return TransferEngine(job, emitter=emitter, control=control).run()


def resume_job(job_id: str, *, emitter: EventEmitter | None = None, control: TransferControl | None = None) -> TransferResult:
    """Re-run a checkpointed job, skipping files already verified as complete."""
    from .checkpoint import load_checkpoint, options_from_dict

    store, meta = load_checkpoint(job_id)
    options = options_from_dict(meta.options)
    job = TransferJob(
        sources=tuple(Path(s) for s in meta.sources),
        destination=Path(meta.destination),
        options=options,
        job_id=meta.job_id,
    )
    engine = TransferEngine(job, emitter=emitter, control=control, checkpoint=store)
    return engine.run()


def retry_failures(
    result: TransferResult,
    destination: Path,
    options: TransferOptions,
    *,
    emitter: EventEmitter | None = None,
    control: TransferControl | None = None,
) -> TransferResult:
    """Re-attempt only the files that failed in a previous run."""
    if not result.failures:
        return result
    sources = tuple(failure.source for failure in result.failures)
    job = TransferJob(
        sources=sources,
        destination=Path(destination),
        options=replace(options, scan_mode=ScanMode.PRESCAN),
    )
    return TransferEngine(job, emitter=emitter, control=control).run()
