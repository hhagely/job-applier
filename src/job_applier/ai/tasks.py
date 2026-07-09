"""Background task execution + progress, kept minimal and dependency-free.

Execution: a stdlib ``ThreadPoolExecutor`` with ``max_workers=1``. Single worker
gives a serialized queue for free — starting a second batch while one runs queues
it instead of racing the AI CLI / subscription. The executor is created lazily and
``shutdown()`` (wired to app teardown so Electron closing cancels in-flight work)
drops it so the next ``start_task`` recreates it — which also keeps the module
singleton reusable across the test suite's many app-lifespan cycles.

Progress: an in-memory registry. Single-user, single-process, and the queue is
re-derivable from the pending-match selection, so a module-level dict needs no
durability. Each worker opens its OWN DB Session (never shares a handle across
threads).
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable, Literal, Optional
from uuid import uuid4

TaskStatus = Literal["running", "done", "error"]

_executor: Optional[ThreadPoolExecutor] = None
_tasks: "dict[str, TaskState]" = {}
_lock = threading.Lock()


def _get_executor() -> ThreadPoolExecutor:
    """Return the shared single-worker executor, creating it on first use (and
    after a prior ``shutdown()``)."""
    global _executor
    with _lock:
        if _executor is None:
            _executor = ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="job-applier-task"
            )
        return _executor


@dataclass
class TaskState:
    id: str
    kind: str
    total: int
    done: int = 0
    errors: list[str] = field(default_factory=list)
    results: list[str] = field(default_factory=list)
    status: TaskStatus = "running"


def start_task(kind: str, total: int, fn: Callable[[TaskState], None]) -> str:
    """Register a task and submit it. ``fn`` receives the ``TaskState`` to update
    as it makes progress and runs on the (single) worker thread."""
    tid = uuid4().hex
    state = TaskState(id=tid, kind=kind, total=total)
    with _lock:
        _tasks[tid] = state
    _get_executor().submit(_run, state, fn)
    return tid


def _run(state: TaskState, fn: Callable[[TaskState], None]) -> None:
    try:
        fn(state)
        state.status = "done"
    except Exception as exc:  # noqa: BLE001 - surface fatal task errors to the UI
        state.status = "error"
        state.errors.append(str(exc))


def get_task(tid: str) -> "TaskState | None":
    return _tasks.get(tid)


def shutdown() -> None:
    """Cancel queued work and drop the executor (idempotent). Wired to app
    teardown so Electron closing tears the worker down; the next ``start_task``
    lazily recreates it, so this is safe to call between app-lifespan cycles."""
    global _executor
    with _lock:
        executor, _executor = _executor, None
    if executor is not None:
        executor.shutdown(wait=False, cancel_futures=True)
