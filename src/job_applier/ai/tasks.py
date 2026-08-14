"""Background task execution + a tiny in-process pub/sub, kept minimal and
dependency-free.

Execution: one stdlib ``ThreadPoolExecutor`` with ``max_workers=1`` per *lane*.
A single worker within a lane gives a serialized queue for free — starting a
second batch while one runs queues it instead of racing.

There are two lanes, because the reason to serialize only applies to one of them.
AI tasks (scoring, drafting) share a worker on purpose: concurrent CLI spawns race
the same provider login and burn the same subscription window. Network tasks
(``ingest``, ``refresh_companies``) have no such constraint, and putting them in
the AI lane meant a multi-minute scrape blocked the user from scoring or drafting
anything until it finished. They now get their own worker, so the two kinds of
work overlap.

Executors are created lazily per lane and ``shutdown()`` (wired to app teardown so
Electron closing cancels in-flight work) drops them all so the next ``start_task``
recreates what it needs — which also keeps the module singleton reusable across
the test suite's many app-lifespan cycles.

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

# Task kinds that only touch the network + DB, never an AI CLI. These get their
# own worker so a long scrape doesn't hold up scoring/drafting; everything else
# shares the AI lane, where serializing is the point (see the module docstring).
NET_KINDS = frozenset({"ingest", "refresh_companies"})
AI_LANE = "ai"
NET_LANE = "net"

# lane name -> its single-worker executor, created on demand.
_executors: "dict[str, ThreadPoolExecutor]" = {}
_tasks: "dict[str, TaskState]" = {}
# Subscribers are callbacks the SSE endpoint registers; each forwards a snapshot
# to one connected client. Guarded by ``_lock`` alongside ``_tasks``/``_executors``.
_subscribers: "set[Callable[[TaskSnapshot], None]]" = set()
_lock = threading.Lock()


def lane_for(kind: str) -> str:
    """Which lane a task ``kind`` runs in."""
    return NET_LANE if kind in NET_KINDS else AI_LANE


def _get_executor(lane: str) -> ThreadPoolExecutor:
    """Return ``lane``'s single-worker executor, creating it on first use (and
    after a prior ``shutdown()``)."""
    with _lock:
        executor = _executors.get(lane)
        if executor is None:
            executor = ThreadPoolExecutor(
                max_workers=1, thread_name_prefix=f"job-applier-{lane}"
            )
            _executors[lane] = executor
        return executor


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
    _get_executor(lane_for(kind)).submit(_run, state, fn)
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
    """Cancel queued work and drop every lane's executor (idempotent). Wired to app
    teardown so Electron closing tears the workers down; the next ``start_task``
    lazily recreates the lane it needs, so this is safe to call between
    app-lifespan cycles."""
    with _lock:
        executors = list(_executors.values())
        _executors.clear()
    for executor in executors:
        executor.shutdown(wait=False, cancel_futures=True)
