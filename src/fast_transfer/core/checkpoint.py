"""SQLite checkpoint store so a killed job can resume without recopying.

One database per job under %LOCALAPPDATA%. Completions are batched, so a crash
loses at most the current batch, never the whole record.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .. import APP_SLUG
from ..utils.paths import case_key, extended_path
from ..utils.system import local_app_data_dir
from .errors import ErrorCode, TransferError
from .models import JobStatus, OperationType, TransferOptions

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS completed (
    source_key TEXT PRIMARY KEY,
    destination TEXT NOT NULL,
    size INTEGER NOT NULL,
    mtime_ns INTEGER,
    completed_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS failed (
    source_key TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    destination TEXT NOT NULL,
    error_code TEXT NOT NULL,
    message TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 1
);
"""


def checkpoint_directory() -> Path:
    return local_app_data_dir(APP_SLUG) / "checkpoints"


def checkpoint_path(job_id: str) -> Path:
    return checkpoint_directory() / f"{job_id}.db"


@dataclass(slots=True)
class CheckpointMeta:
    """The job description stored alongside the completion list."""

    job_id: str
    operation: OperationType
    sources: tuple[str, ...]
    destination: str
    options: dict[str, Any]
    created_at: float
    updated_at: float
    status: JobStatus = JobStatus.PENDING
    total_files: int = 0
    total_bytes: int = 0
    completed_files: int = 0
    completed_bytes: int = 0


class CheckpointStore:
    """Records completed and failed files for one job."""

    def __init__(self, job_id: str, path: Path | None = None, *, batch_size: int = 200) -> None:
        self.job_id = job_id
        self.path = path or checkpoint_path(job_id)
        self._batch_size = max(1, batch_size)
        self._lock = threading.Lock()
        self._pending: list[tuple[str, str, int, int | None, float]] = []
        self._connection: sqlite3.Connection | None = None
        self._completed_keys: set[str] = set()

    # -- lifecycle ---------------------------------------------------------

    def open(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(str(self.path), check_same_thread=False)
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=NORMAL")
            connection.executescript(_SCHEMA)
            connection.commit()
            self._connection = connection
        except sqlite3.Error as exc:
            raise TransferError(
                ErrorCode.CHECKPOINT_ERROR,
                f"Could not open the resume checkpoint: {exc}",
                path=self.path,
                cause=exc,
            ) from exc
        self._load_completed_keys()

    def close(self) -> None:
        with self._lock:
            self._flush_locked()
            if self._connection is not None:
                try:
                    self._connection.commit()
                    self._connection.close()
                finally:
                    self._connection = None

    def __enter__(self) -> CheckpointStore:
        self.open()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def delete(self) -> None:
        """Remove the checkpoint once the job finishes cleanly."""
        self.close()
        for suffix in ("", "-wal", "-shm"):
            candidate = Path(str(self.path) + suffix)
            try:
                if candidate.exists():
                    candidate.unlink()
            except OSError:
                continue

    # -- meta --------------------------------------------------------------

    def write_meta(self, meta: CheckpointMeta) -> None:
        payload = asdict(meta)
        payload["operation"] = meta.operation.value
        payload["status"] = meta.status.value
        payload["sources"] = list(meta.sources)
        self._set_meta("job", json.dumps(payload, ensure_ascii=False))
        self._set_meta("schema_version", str(SCHEMA_VERSION))

    def read_meta(self) -> CheckpointMeta | None:
        raw = self._get_meta("job")
        if raw is None:
            return None
        try:
            data = json.loads(raw)
            return CheckpointMeta(
                job_id=data["job_id"],
                operation=OperationType(data["operation"]),
                sources=tuple(data["sources"]),
                destination=data["destination"],
                options=data.get("options", {}),
                created_at=data.get("created_at", 0.0),
                updated_at=data.get("updated_at", 0.0),
                status=JobStatus(data.get("status", JobStatus.PENDING.value)),
                total_files=data.get("total_files", 0),
                total_bytes=data.get("total_bytes", 0),
                completed_files=data.get("completed_files", 0),
                completed_bytes=data.get("completed_bytes", 0),
            )
        except (json.JSONDecodeError, KeyError, ValueError) as exc:
            raise TransferError(
                ErrorCode.CHECKPOINT_ERROR,
                "The resume checkpoint is corrupted and cannot be read.",
                path=self.path,
                cause=exc,
            ) from exc

    def update_progress(
        self, status: JobStatus, completed_files: int, completed_bytes: int
    ) -> None:
        meta = self.read_meta()
        if meta is None:
            return
        meta.status = status
        meta.completed_files = completed_files
        meta.completed_bytes = completed_bytes
        meta.updated_at = time.time()
        self.write_meta(meta)

    def _set_meta(self, key: str, value: str) -> None:
        with self._lock:
            connection = self._require_connection()
            connection.execute(
                "INSERT INTO meta(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )
            connection.commit()

    def _get_meta(self, key: str) -> str | None:
        with self._lock:
            connection = self._require_connection()
            row = connection.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
            return row[0] if row else None

    # -- completions -------------------------------------------------------

    def mark_completed(
        self, source: Path, destination: Path, size: int, mtime_ns: int | None = None
    ) -> None:
        key = case_key(source)
        with self._lock:
            self._completed_keys.add(key)
            self._pending.append((key, str(destination), size, mtime_ns, time.time()))
            if len(self._pending) >= self._batch_size:
                self._flush_locked()

    def mark_failed(
        self, source: Path, destination: Path, error_code: str, message: str, attempts: int = 1
    ) -> None:
        with self._lock:
            connection = self._require_connection()
            connection.execute(
                "INSERT INTO failed(source_key, source, destination, error_code, message, attempts) "
                "VALUES(?, ?, ?, ?, ?, ?) ON CONFLICT(source_key) DO UPDATE SET "
                "error_code=excluded.error_code, message=excluded.message, attempts=excluded.attempts",
                (case_key(source), str(source), str(destination), error_code, message, attempts),
            )
            connection.commit()

    def clear_failure(self, source: Path) -> None:
        with self._lock:
            connection = self._require_connection()
            connection.execute("DELETE FROM failed WHERE source_key = ?", (case_key(source),))
            connection.commit()

    def is_completed(self, source: Path) -> bool:
        with self._lock:
            if case_key(source) in self._completed_keys:
                return True
        return False

    def flush(self) -> None:
        with self._lock:
            self._flush_locked()

    def _flush_locked(self) -> None:
        if not self._pending or self._connection is None:
            return
        self._connection.executemany(
            "INSERT INTO completed(source_key, destination, size, mtime_ns, completed_at) "
            "VALUES(?, ?, ?, ?, ?) ON CONFLICT(source_key) DO UPDATE SET "
            "destination=excluded.destination, size=excluded.size, "
            "mtime_ns=excluded.mtime_ns, completed_at=excluded.completed_at",
            self._pending,
        )
        self._connection.commit()
        self._pending.clear()

    def _load_completed_keys(self) -> None:
        with self._lock:
            connection = self._require_connection()
            rows = connection.execute("SELECT source_key FROM completed").fetchall()
            self._completed_keys = {row[0] for row in rows}

    def completed_count(self) -> int:
        self.flush()
        with self._lock:
            connection = self._require_connection()
            return int(connection.execute("SELECT COUNT(*) FROM completed").fetchone()[0])

    def failures(self) -> list[dict[str, Any]]:
        with self._lock:
            connection = self._require_connection()
            rows = connection.execute(
                "SELECT source, destination, error_code, message, attempts FROM failed"
            ).fetchall()
        return [
            {
                "source": row[0],
                "destination": row[1],
                "error_code": row[2],
                "message": row[3],
                "attempts": row[4],
            }
            for row in rows
        ]

    def verify_completed(self, source: Path, destination: Path, size: int) -> bool:
        """On resume, trust the record only if the destination still matches.

        Anything uncertain returns False so the file is transferred again --
        recopying a file is cheap next to silently keeping a truncated one.
        """
        if not self.is_completed(source):
            return False
        try:
            st = os.stat(extended_path(destination))
        except OSError:
            return False
        return st.st_size == size

    def _require_connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise TransferError(
                ErrorCode.CHECKPOINT_ERROR, "The checkpoint database is not open.", path=self.path
            )
        return self._connection


def list_checkpoints() -> list[CheckpointMeta]:
    """Every resumable job on this machine, newest first. Corrupt files are skipped."""
    directory = checkpoint_directory()
    if not directory.exists():
        return []
    jobs: list[CheckpointMeta] = []
    for database in sorted(directory.glob("*.db")):
        store = CheckpointStore(database.stem, database)
        try:
            store.open()
            meta = store.read_meta()
            if meta is not None:
                jobs.append(meta)
        except TransferError:
            continue
        finally:
            store.close()
    return sorted(jobs, key=lambda m: m.updated_at, reverse=True)


def load_checkpoint(job_id: str) -> tuple[CheckpointStore, CheckpointMeta]:
    """Open an existing checkpoint for resume."""
    path = checkpoint_path(job_id)
    if not path.exists():
        raise TransferError(
            ErrorCode.CHECKPOINT_ERROR, f"No resumable job with id '{job_id}' was found."
        )
    store = CheckpointStore(job_id, path)
    store.open()
    meta = store.read_meta()
    if meta is None:
        store.close()
        raise TransferError(
            ErrorCode.CHECKPOINT_ERROR, f"The checkpoint for job '{job_id}' has no job record."
        )
    return store, meta


def options_to_dict(options: TransferOptions) -> dict[str, Any]:
    """Serialise options for the checkpoint, converting enums to their values."""
    data: dict[str, Any] = {}
    for field_name in options.__slots__:
        value = getattr(options, field_name)
        if hasattr(value, "value"):
            value = value.value
        elif isinstance(value, tuple):
            value = list(value)
        data[field_name] = value
    return data


def options_from_dict(data: dict[str, Any]) -> TransferOptions:
    """Rebuild options saved by `options_to_dict`, ignoring unknown keys."""
    from .models import ConflictPolicy, ScanMode, SymlinkPolicy, VerifyMode

    enum_fields = {
        "operation": OperationType,
        "verify": VerifyMode,
        "conflict": ConflictPolicy,
        "scan_mode": ScanMode,
        "symlink_policy": SymlinkPolicy,
    }
    tuple_fields = {"include_patterns", "exclude_patterns"}
    known = set(TransferOptions.__slots__)
    kwargs: dict[str, Any] = {}
    for key, value in data.items():
        if key not in known:
            continue
        if key in enum_fields and value is not None:
            kwargs[key] = enum_fields[key](value)
        elif key in tuple_fields and value is not None:
            kwargs[key] = tuple(value)
        else:
            kwargs[key] = value
    return TransferOptions(**kwargs)
