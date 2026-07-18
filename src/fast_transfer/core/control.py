"""Thread-safe cancel / pause control shared by every worker in a job."""

from __future__ import annotations

import threading

from .errors import CancelledError


class TransferControl:
    """One instance per job. Workers poll it between chunks and between files.

    Pause is modelled as a `threading.Event` that is *set* while running, so the
    fast path (`wait()` on a set event) is close to free.
    """

    def __init__(self) -> None:
        self._cancel = threading.Event()
        self._running = threading.Event()
        self._running.set()

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    @property
    def paused(self) -> bool:
        return not self._running.is_set()

    def cancel(self) -> None:
        self._cancel.set()
        # Release anyone blocked in pause so they can observe the cancel.
        self._running.set()

    def pause(self) -> None:
        if not self._cancel.is_set():
            self._running.clear()

    def resume(self) -> None:
        self._running.set()

    def reset(self) -> None:
        self._cancel.clear()
        self._running.set()

    def wait_if_paused(self, timeout: float | None = None) -> None:
        """Block while paused. Returns immediately when running or cancelled."""
        self._running.wait(timeout)

    def raise_if_cancelled(self) -> None:
        if self._cancel.is_set():
            raise CancelledError()

    def checkpoint(self) -> None:
        """Single call for the worker inner loop: honour pause, then cancel."""
        if not self._running.is_set():
            self._running.wait()
        if self._cancel.is_set():
            raise CancelledError()
