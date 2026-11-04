"""Source-adapter contract.

The shared ``RawJob`` vocabulary and the date-parsing helpers now live in
``job_applier.contracts`` (dependency-free, so ``filters`` can import them without
a cycle). They are re-exported here so the adapters can keep importing everything
they need from one place — ``from job_applier.sources.base import RawJob, ...``.
"""

import logging
import re
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from itertools import islice
from typing import Protocol, TypeVar

from job_applier.contracts import RawJob, parse_date_multi, parse_iso_date

__all__ = [
    "BOARD_WORKERS",
    "RawJob",
    "SourceAdapter",
    "TITLE_GATE",
    "fetch_boards",
    "looks_remote",
    "parse_date_multi",
    "parse_iso_date",
]

log = logging.getLogger(__name__)

# Boards fetched at once within one source. Most sources serve every board from a
# single API host, so this is also roughly the load that host sees from us.
BOARD_WORKERS = 8

Board = TypeVar("Board")


def fetch_boards(
    boards: Iterable[Board],
    fetch_one: Callable[[Board], Iterable[RawJob]],
    *,
    source: str,
    workers: int = BOARD_WORKERS,
) -> Iterator[RawJob]:
    """Run ``fetch_one`` over ``boards`` concurrently, yielding each board's jobs as
    it finishes, so a slow board holds up nothing but itself.

    Only ``workers`` boards are in progress at a time and the next one starts when
    the consumer comes back for more, which bounds how many fetched boards sit in
    memory while ingest writes a batch. ``fetch_one`` runs on a worker thread, so it
    must not touch the DB; the caller's writes stay on the consuming thread.

    A board that raises is logged and skipped, keeping the jobs it had already
    produced. Job order across boards varies from run to run.
    """

    def drain(board: Board) -> list[RawJob]:
        jobs: list[RawJob] = []
        try:
            jobs.extend(fetch_one(board))
        except Exception as e:  # noqa: BLE001 - one board can't stop the others
            log.warning("%s[%s] failed, skipping the rest of the board: %s", source, board, e)
        return jobs

    remaining = iter(boards)
    running: set[Future[list[RawJob]]] = set()
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix=source) as pool:
        while True:
            running.update(
                pool.submit(drain, board)
                for board in islice(remaining, workers - len(running))
            )
            if not running:
                return
            done, running = wait(running, return_when=FIRST_COMPLETED)
            for future in done:
                yield from future.result()


def looks_remote(*texts: str | None) -> bool:
    """True if any fragment contains "remote" (case-insensitive).

    The plain-substring half of the remote check nearly every adapter does. The
    per-source parts that vary — an explicit ``isRemote``/``workplaceType`` flag,
    a hybrid/on-site exclusion, or a term list beyond just "remote" — stay in the
    adapter; this only collapses the repeated ``"remote" in x.lower()`` idiom.
    """
    return any("remote" in (t or "").lower() for t in texts)

# Senior + engineering title gate — a cheap pre-filter shared by the Workday and
# Oracle adapters, which apply it before an expensive per-posting detail fetch.
# Anything that passes still runs the full filter pipeline downstream, so the
# gate only has to be roughly right. Kept here (not per-adapter) so the two
# copies can't drift.
TITLE_GATE = re.compile(
    r"\b(senior|sr\.?|staff|principal|lead|architect|distinguished|head\s+of)\b.*?"
    r"\b(engineer|developer|architect|sde|swe)\b",
    re.IGNORECASE,
)


class SourceAdapter(Protocol):
    name: str

    def fetch(self) -> Iterable[RawJob]: ...
