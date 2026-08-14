"""Background tasks run in two lanes: AI work serializes, network work doesn't
block it."""

from __future__ import annotations

import threading

from job_applier.ai import tasks


def test_a_network_task_does_not_block_an_ai_task():
    """A scrape in flight must not stop the user scoring or drafting.

    Before the lanes were split these shared one worker, so `ingest` — which can
    run for minutes — queued every AI task behind it.
    """
    tasks.shutdown()
    running = threading.Event()
    release = threading.Event()
    ai_done = threading.Event()

    def slow_scrape(_state):
        running.set()
        release.wait(timeout=10)

    try:
        tasks.start_task("ingest", 1, slow_scrape)
        assert running.wait(timeout=10), "the net task never started"

        tasks.start_task("score_pending", 1, lambda _s: ai_done.set())
        # Would time out if the two kinds still shared a single worker.
        assert ai_done.wait(timeout=10), "AI task was queued behind the scrape"
    finally:
        release.set()
        tasks.shutdown()


def test_ai_tasks_still_serialize():
    """The AI lane keeps its single worker on purpose — concurrent CLI spawns
    race the same provider login and subscription window."""
    tasks.shutdown()
    first_running = threading.Event()
    release = threading.Event()
    second_started = threading.Event()

    def slow_first(_state):
        first_running.set()
        release.wait(timeout=10)

    try:
        tasks.start_task("score_pending", 1, slow_first)
        assert first_running.wait(timeout=10)

        tasks.start_task("draft_batch", 1, lambda _s: second_started.set())
        # Still queued behind the first AI task, so it must NOT have run yet.
        assert not second_started.wait(timeout=0.5)

        release.set()
        assert second_started.wait(timeout=10), "queued AI task never ran"
    finally:
        release.set()
        tasks.shutdown()
