"""Background task execution + progress, kept minimal and dependency-free.

Execution: a stdlib ``ThreadPoolExecutor`` with ``max_workers=1``. Single worker
gives a serialized queue for free — starting a second batch while one runs queues
it instead of racing the AI CLI / subscription. ``shutdown()`` gives Electron a
clean teardown later.

Progress: an in-memory registry. Single-user, single-process, and the queue is
re-derivable from the pending-match selection, so a module-level dict needs no
durability. Each worker opens its OWN DB Session (never shares a handle across
threads).
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable, Literal
from uuid import uuid4

TaskStatus = Literal["running", "done", "error"]

_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="job-applier-task")
_tasks: "dict[str, TaskState]" = {}
_lock = threading.Lock()


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
    _executor.submit(_run, state, fn)
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
    _executor.shutdown(wait=False, cancel_futures=True)
