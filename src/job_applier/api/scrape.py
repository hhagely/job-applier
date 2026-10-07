"""Scrape endpoints: start an ingest run, and the company job-board list it
reads from (coverage counts and the refresh that discovers new boards).

Both runs are background tasks in the network lane, polled through the shared
task stream; neither needs an AI provider, just network access.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import Integer, cast, func
from sqlmodel import Session, select

from job_applier import ingest
from job_applier.ai import tasks as ai_tasks
from job_applier.api.schemas import CompanyCoverageOut, StartTaskOut
from job_applier.contracts import parse_iso_date
from job_applier.models.db import SourceSlug, engine, get_session, get_setting, set_setting
from job_applier.sources import refresh as refresh_mod
from job_applier.sources.refresh import refresh_slugs

router = APIRouter(tags=["scrape"])


def _run_ingest_task(state: "ai_tasks.TaskState") -> None:
    """Worker body: pull jobs from every source, reporting per-source progress."""

    def _cb(done: int, total: int, name: str, stats: ingest.IngestStats) -> None:
        state.total = total
        state.done = done
        state.results.append(
            f"{name}: {stats.inserted} new / {stats.passed_filter} passed (running total)"
        )
        state.publish()

    def _matched(name: str, m) -> None:  # noqa: ANN001
        state.results.append(f"{name}: {m.passed} new in queue, {m.manual} to review")
        state.publish()

    stats = ingest.run_ingest(progress_cb=_cb, match_cb=_matched)
    state.results.append(
        f"done: {stats.fetched} fetched, {stats.inserted} new postings stored"
    )


@router.post("/api/ingest", response_model=StartTaskOut)
def start_ingest() -> StartTaskOut:
    """Kick off a background scrape of every source. Poll GET /api/ai/tasks/{id}
    for per-source progress. Needs no AI provider — just network access."""
    from job_applier.sources import get_all_sources

    total = len(get_all_sources())
    # Not pinned to a profile: a scrape stores postings for everyone and matches
    # every profile (each with its own pinned session), and a pin would point
    # run_ingest's profile bootstrap at a profile deleted while this queued.
    task_id = ai_tasks.start_task("ingest", total, _run_ingest_task)
    return StartTaskOut(task_id=task_id)


# When the company-board discovery pass last ran. Stored as a setting rather than
# derived from SourceSlug.updated_at because a run that finds nothing new still
# counts as "we checked" — and that distinction is the whole point of showing it.
COMPANY_CHECKED_KEY = "companies_last_checked_at"


@router.get("/api/company-coverage", response_model=CompanyCoverageOut)
def company_coverage(session: Session = Depends(get_session)) -> CompanyCoverageOut:
    """How many company job boards ingest watches, split by source, plus when the
    list was last checked for new ones."""
    # `enabled` is a Boolean column, so SUM() over it inherits the Boolean result
    # processor and every non-zero total collapses to True (=1). Cast to Integer so
    # the sum stays a count.
    rows = session.exec(
        select(
            SourceSlug.source,
            func.count(SourceSlug.id),
            func.sum(cast(SourceSlug.enabled, Integer)),
        ).group_by(SourceSlug.source)
    ).all()
    by_source = {source: int(n) for source, n, _ in rows}
    # The checked-at setting is a free-form string column, so parse it leniently:
    # a hand-edited or half-written value must degrade to "never checked", not 500
    # this endpoint. /search loads it as a page dependency, so a hard failure here
    # would lock the user out of the very page that resets the list.
    last = parse_iso_date(get_setting(session, COMPANY_CHECKED_KEY))
    return CompanyCoverageOut(
        total=sum(by_source.values()),
        enabled=sum(int(en or 0) for _, _, en in rows),
        by_source=dict(sorted(by_source.items(), key=lambda kv: -kv[1])),
        last_checked_at=last,
    )


def _run_refresh_companies_task(state: "ai_tasks.TaskState", reverify: bool) -> None:
    """Worker body: discover + verify new company job boards, reporting one step
    per source pass. Stamps the checked-at setting only on success, so a failed
    run doesn't make a stale list look fresh."""

    def _cb(done: int, total: int, label: str) -> None:
        state.total = total
        state.done = done
        state.results.append(label)
        state.publish()

    stats = refresh_slugs(reverify_existing=reverify, progress_cb=_cb)
    added = (
        stats.gh_added
        + stats.lv_added
        + stats.wk_added
        + stats.sr_added
        + stats.ashby_added
    )
    disabled = (
        stats.gh_disabled
        + stats.lv_disabled
        + stats.ashby_disabled
        + stats.workday_disabled
        + stats.wk_disabled
        + stats.sr_disabled
    )
    summary = f"done: {added} new compan{'y' if added == 1 else 'ies'} added"
    if reverify:
        summary += f", {disabled} dead board{'' if disabled == 1 else 's'} disabled"
    state.results.append(summary)

    with Session(engine()) as own:
        set_setting(own, COMPANY_CHECKED_KEY, datetime.now(timezone.utc).isoformat())


@router.post("/api/company-coverage/refresh", response_model=StartTaskOut)
def start_company_refresh(reverify: bool = False) -> StartTaskOut:
    """Kick off a background pass that finds company job boards not yet watched
    (and, with ``reverify``, disables ones that no longer respond). Poll
    GET /api/ai/tasks/{id} for progress. Needs no AI provider — just network."""
    running = ai_tasks.active_task("refresh_companies")
    if running is not None:
        # Already in flight — hand back the live task instead of queueing a second
        # pass over the same feed.
        return StartTaskOut(task_id=running.id)
    total = (
        refresh_mod.REFRESH_STEPS_REVERIFY if reverify else refresh_mod.REFRESH_STEPS
    )
    task_id = ai_tasks.start_task(
        "refresh_companies",
        total,
        lambda state: _run_refresh_companies_task(state, reverify),
    )
    return StartTaskOut(task_id=task_id)
