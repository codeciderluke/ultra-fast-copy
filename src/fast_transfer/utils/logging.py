"""Structured logging on a background queue so file I/O never blocks a worker."""

from __future__ import annotations

import atexit
import logging
import logging.handlers
import queue
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from .. import APP_SLUG
from .system import local_app_data_dir

LOGGER_NAME = "fast_transfer"

_listener: logging.handlers.QueueListener | None = None
_configured = False

# Anonymise user-identifying path segments when exporting logs.
_USER_PATH_RE = re.compile(r"([A-Za-z]:\\Users\\)([^\\]+)", re.IGNORECASE)
_UNC_RE = re.compile(r"(\\\\)([^\\]+)(\\)")


def log_directory() -> Path:
    return local_app_data_dir(APP_SLUG) / "logs"


def default_log_file() -> Path:
    return log_directory() / f"ufcopy-{datetime.now():%Y%m%d}.log"


class JobAdapter(logging.LoggerAdapter):
    """Stamps every record with its job id."""

    def process(self, msg: Any, kwargs: Any) -> tuple[Any, Any]:
        job_id = self.extra.get("job_id", "-") if self.extra else "-"
        return f"[job {job_id}] {msg}", kwargs


def configure_logging(
    level: str | int = "INFO",
    log_file: Path | None = None,
    *,
    console: bool = False,
    retention_days: int = 30,
) -> logging.Logger:
    """Install the queue handler. Safe to call more than once."""
    global _listener, _configured

    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(level)
    logger.propagate = False

    if _configured:
        return logger

    target = log_file or default_log_file()
    handlers: list[logging.Handler] = []
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.TimedRotatingFileHandler(
            target,
            when="midnight",
            backupCount=max(1, retention_days),
            encoding="utf-8",
            delay=True,
        )
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-7s %(name)s %(message)s")
        )
        handlers.append(file_handler)
    except OSError:
        # A read-only or missing log directory must not stop the transfer.
        pass

    if console:
        stream = logging.StreamHandler()
        stream.setFormatter(logging.Formatter("%(levelname)-7s %(message)s"))
        handlers.append(stream)

    if handlers:
        log_queue: queue.Queue[logging.LogRecord] = queue.Queue(-1)
        logger.addHandler(logging.handlers.QueueHandler(log_queue))
        _listener = logging.handlers.QueueListener(log_queue, *handlers, respect_handler_level=True)
        _listener.start()
        atexit.register(shutdown_logging)

    _configured = True
    return logger


def shutdown_logging() -> None:
    global _listener, _configured
    if _listener is not None:
        _listener.stop()
        _listener = None
    logger = logging.getLogger(LOGGER_NAME)
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
    _configured = False


def get_logger(job_id: str | None = None) -> logging.Logger | JobAdapter:
    logger = logging.getLogger(LOGGER_NAME)
    if job_id is None:
        return logger
    return JobAdapter(logger, {"job_id": job_id})


def anonymize_path(text: str) -> str:
    """Replace the user name in local and UNC paths before sharing a log."""
    text = _USER_PATH_RE.sub(r"\1<user>", text)
    return _UNC_RE.sub(r"\1<host>\3", text)


def anonymize_log_file(source: Path, destination: Path) -> Path:
    """Write an anonymised copy of a log file for support hand-off."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    with (
        source.open("r", encoding="utf-8", errors="replace") as reader,
        destination.open("w", encoding="utf-8") as writer,
    ):
        for line in reader:
            writer.write(anonymize_path(line))
    return destination
