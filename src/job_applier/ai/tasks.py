"""Background task execution + a tiny in-process pub/sub, kept minimal and
dependency-free.

Execution: a stdlib ``ThreadPoolExecutor`` with ``max_workers=1``. Single worker
gives a serialized queue for free — starting a second batch while one runs queues
it instead of racing the AI CLI / subscription. The executor is created lazily and
``shutdown()`` (wired to app teardown so Electron closing cancels in-flight work)
drops it so the next ``start_task`` recreates it — which also keeps the module
singleton reusable across the test suite's many app-lifespan cycles.

Progress: an in-memory registry plus a subscriber list. Workers ``publish`` a
snapshot on start, on every progress step, and on the terminal transition; the SSE
endpoint ``subscribe``s a callback and forwards each snapshot to a connected
client (push, not poll). Single-user, single-process, and the queue is
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

# A serialized JSON-safe view of a TaskState, as pushed to subscribers.
TaskSnapshot = dict

_executor: Optional[ThreadPoolExecutor] = None
_tasks: "dict[str, TaskState]" = {}
# Subscribers are callbacks the SSE endpoint registers; each forwards a snapshot
# to one connected client. Guarded by ``_lock`` alongside ``_tasks``/``_executor``.
_subscribers: "set[Callable[[TaskSnapshot], None]]" = set()
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
    # Optional discriminator within a kind — e.g. the job id for a per-job draft —
    # so a client can track "this job's draft" rather than "any draft".
    ref: Optional[str] = None

    def publish(self) -> None:
        """Fan this task's current state out to every subscriber. Workers call
        this from the worker thread after mutating progress; a convenience alias
        for the module-level :func:`publish`."""
        publish(self)


def snapshot(state: TaskState) -> TaskSnapshot:
    """A JSON-safe copy of ``state`` (lists copied so a subscriber can't observe
    a later in-place mutation mid-serialization)."""
    return {
        "id": state.id,
        "kind": state.kind,
        "total": state.total,
        "done": state.done,
        "status": state.status,
        "errors": list(state.errors),
        "results": list(state.results),
        "ref": state.ref,
    }


def subscribe(callback: "Callable[[TaskSnapshot], None]") -> None:
    """Register ``callback`` to receive every future published snapshot. The SSE
    handler passes a thread-safe forwarder here; pair with :func:`unsubscribe`."""
    with _lock:
        _subscribers.add(callback)


def unsubscribe(callback: "Callable[[TaskSnapshot], None]") -> None:
    with _lock:
        _subscribers.discard(callback)


def publish(state: TaskState) -> None:
    """Snapshot ``state`` and hand it to each subscriber. Called from the worker
    thread, so subscribers MUST be non-blocking and thread-safe (the SSE
    forwarder just schedules a put on the request's event loop). A raising
    subscriber is swallowed so one dead client can't break the worker."""
    event = snapshot(state)
    with _lock:
        callbacks = list(_subscribers)
    for cb in callbacks:
        try:
            cb(event)
        except Exception:  # noqa: BLE001 - a dead subscriber can't break the worker
            pass


def start_task(
    kind: str,
    total: int,
    fn: Callable[[TaskState], None],
    *,
    ref: Optional[str] = None,
) -> str:
    """Register a task and submit it. ``fn`` receives the ``TaskState`` to update
    as it makes progress and runs on the (single) worker thread. ``ref`` is an
    optional per-kind discriminator (e.g. a job id) echoed to subscribers."""
    tid = uuid4().hex
    state = TaskState(id=tid, kind=kind, total=total, ref=ref)
    with _lock:
        _tasks[tid] = state
    # Announce the task immediately so an already-connected client sees it appear
    # before the first progress step.
    publish(state)
    _get_executor().submit(_run, state, fn)
    return tid


def _run(state: TaskState, fn: Callable[[TaskState], None]) -> None:
    try:
        fn(state)
        state.status = "done"
    except Exception as exc:  # noqa: BLE001 - surface fatal task errors to the UI
        state.status = "error"
        state.errors.append(str(exc))
    finally:
        # Always emit the terminal snapshot so subscribers stop waiting.
        publish(state)


def get_task(tid: str) -> "TaskState | None":
    return _tasks.get(tid)


def active_task(kind: str) -> "TaskState | None":
    """The running task of ``kind``, if one is in flight. Used to dedupe starts —
    e.g. score-pending returns the live run instead of queueing a duplicate."""
    with _lock:
        for state in _tasks.values():
            if state.kind == kind and state.status == "running":
                return state
    return None


def active_snapshots() -> "list[TaskSnapshot]":
    """Snapshots of every currently-running task, for a client that just connected
    (or reconnected) to re-attach its progress UI."""
    with _lock:
        return [snapshot(s) for s in _tasks.values() if s.status == "running"]


def shutdown() -> None:
    """Cancel queued work and drop the executor (idempotent). Wired to app
    teardown so Electron closing tears the worker down; the next ``start_task``
    lazily recreates it, so this is safe to call between app-lifespan cycles."""
    global _executor
    with _lock:
        executor, _executor = _executor, None
    if executor is not None:
        executor.shutdown(wait=False, cancel_futures=True)
