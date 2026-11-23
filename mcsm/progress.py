"""Progress and cancelling for long jobs (the app's loading bar and its Cancel button).

Long loops call :func:`report` (``done`` of ``total``, and what is happening). Nothing happens unless a
job set a :class:`Job` for the current thread (the app does, for each background job): then the report
reaches it, and once the owner pressed Cancel the next report (or :func:`check`) raises
:class:`Cancelled`. Reports come between files, never in the middle of writing one, so a cancelled job
leaves no half-written file behind.

:class:`Cancelled` is a ``BaseException`` (like ``KeyboardInterrupt``), so the many "skip a bad file and
carry on" handlers (``except Exception``) don't swallow it.
"""

from __future__ import annotations

import threading
import time
from typing import Callable

_local = threading.local()


class Cancelled(BaseException):
    """The owner cancelled the job."""


class Job:
    """One job's progress (read by the app) and its cancel flag (set by the app)."""

    def __init__(self, on_report: Callable[[int | None, int | None, str], None] | None = None,
                 min_interval: float = 0.1):
        self.cancelled = threading.Event()
        self.on_report = on_report
        self.min_interval = min_interval
        self._last = 0.0
        self.done: int | None = None
        self.total: int | None = None
        self.text = ""

    def cancel(self) -> None:
        self.cancelled.set()

    def report(self, done: int | None, total: int | None, text: str = "") -> None:
        if self.cancelled.is_set():
            raise Cancelled()
        self.done, self.total = done, total
        if text:
            self.text = text
        now = time.monotonic()
        last_one = total is not None and done is not None and done >= total
        if self.on_report and (now - self._last >= self.min_interval or last_one):
            self._last = now
            self.on_report(done, total, self.text)


def current() -> Job | None:
    return getattr(_local, "job", None)


def set_job(job: Job | None) -> None:
    """Make *job* receive this thread's reports (None: stop)."""
    _local.job = job


def report(done: int | None = None, total: int | None = None, text: str = "") -> None:
    """Progress of the current job (no job: nothing). Raises :class:`Cancelled` after Cancel."""
    job = current()
    if job is not None:
        job.report(done, total, text)


def check() -> None:
    """Raise :class:`Cancelled` if the current job was cancelled."""
    job = current()
    if job is not None and job.cancelled.is_set():
        raise Cancelled()
