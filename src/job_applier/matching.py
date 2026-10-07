"""Per-profile matching: run each profile's personal rules over stored postings.

The scrape stores every posting that passes the *shared* rules once, for
everyone (see ``ingest``). Matching is the second, local step: for one profile,
evaluate its blacklist and personal filter (``filters.evaluate_profile``) over
the stored postings and record the verdict as a ``JobProfileLink`` — passed,
manual, or dropped. A profile's queue is its passed/manual links.

Recording drops too is what keeps re-scrapes cheap: a link's existence means
"this profile has evaluated this posting", so a normal run only evaluates
postings the profile hasn't seen. ``rematch=True`` re-evaluates every recent
posting plus every one the profile already has a verdict on — after its
criteria, home state, or blacklist change, or when it's created — without
touching the network. ~0.65 ms per posting, so a full
re-match of a ~15k-posting store is ~10 s: callers run it as a background task.

Same write discipline as ingest (never hold SQLite's write lock across slow
work): candidates are read and evaluated with no transaction open, and verdicts
are written ``MATCH_BATCH`` at a time in short transactions.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, func, not_, or_
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlmodel import Session, select

from job_applier.ai import tasks
from job_applier.dedupe import normalize_company
from job_applier.filters import FilterConfig, config_for, evaluate_profile
from job_applier.models.db import (
    Application,
    ApplicationStatus,
    BlacklistedCompany,
    Company,
    FilterStatus,
    JobPosting,
    JobProfileLink,
    SearchProfile,
    engine,
)
from job_applier.sources.base import RawJob

log = logging.getLogger(__name__)

# Postings older than this aren't matched: they're as good as closed, and the
# ingest stale rule uses the same window.
MATCH_WINDOW_DAYS = 30

MATCH_BATCH = 500

BLACKLISTED_REASON = "company is on your blacklist"


@dataclass
class MatchStats:
    evaluated: int = 0
    passed: int = 0
    manual: int = 0
    dropped: int = 0
    # Re-match only: an acted-on posting whose new verdict would have hidden it.
    kept_acted: int = 0


def _pinned(profile_id: int) -> Session:
    """A session that reads and writes ``profile_id``'s rows (blacklist, links,
    applications) regardless of which profile is active."""
    s = Session(engine())
    s.info["profile_id"] = profile_id
    return s


def _to_raw(row) -> RawJob:  # noqa: ANN001
    return RawJob(
        source=row.source,
        source_id=row.source_id,
        url=row.url,
        title=row.title,
        company_name=row.company_name or "",
        description=row.description or "",
        location=row.location,
        remote=row.remote,
        tags=list(row.tags or []),
    )


Verdict = tuple[FilterStatus, str | None]


def _blacklisted(row, blacklist: frozenset[str]) -> bool:  # noqa: ANN001
    return bool(blacklist) and normalize_company(row.company_name or "") in blacklist


def _verdict(row, cfg: FilterConfig, blacklist: frozenset[str]) -> Verdict:  # noqa: ANN001
    if _blacklisted(row, blacklist):
        return FilterStatus.dropped, BLACKLISTED_REASON
    result = evaluate_profile(_to_raw(row), cfg)
    return result.status, result.reason


def match_profile(
    profile_id: int,
    *,
    rematch: bool = False,
    progress_cb: Callable[[int, int], None] | None = None,
    now: datetime | None = None,
) -> MatchStats:
    """Evaluate ``profile_id``'s rules over stored postings and record verdicts.

    Without ``rematch``, only recent postings the profile has no verdict for.
    With it, every recent posting plus every one it already has a verdict on; an
    existing verdict is overwritten except that a posting the profile has
    already acted on (has a non-``archived`` ``Application``) never goes from
    visible to dropped — editing your criteria narrows what's *new*, it doesn't
    hide a job you applied to, and the kept verdict keeps its reason.
    ``archived`` is the machine-owned bucket for postings never pursued (mostly
    auto-archived on a low score), so those drop like any other.

    A pruned posting (``make prune`` cleared its description and raw) has
    nothing left for the personal rules to read, so it's never newly matched;
    on a re-match its existing verdict stands unless the blacklist, which needs
    only the company name, now hides it.

    ``progress_cb(done, total)`` is called after each written batch.
    """
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=MATCH_WINDOW_DAYS)
    with _pinned(profile_id) as s:
        profile = s.get(SearchProfile, profile_id)
        if profile is None:
            # Deleted while this run was queued: writing links for it would undo
            # delete_profile's cleanup (and hand them to a reused id).
            return MatchStats()
        cfg = config_for(profile)
        # Explicit profile filters below, not just the session pin: these feed
        # subqueries and bulk writes, where being literal beats being clever.
        blacklist = frozenset(
            s.exec(
                select(BlacklistedCompany.normalized_name).where(
                    BlacklistedCompany.profile_id == profile_id
                )
            ).all()
        )
        # Pruned = description and raw both cleared; a posting whose source
        # simply sent no description still has its raw.
        pruned = and_(
            JobPosting.description == "",
            func.json(JobPosting.raw).in_(["{}", "null"]),
        )
        stmt = (
            select(
                JobPosting.id,
                JobPosting.source,
                JobPosting.source_id,
                JobPosting.url,
                JobPosting.title,
                JobPosting.description,
                JobPosting.location,
                JobPosting.remote,
                JobPosting.tags,
                Company.name.label("company_name"),
                pruned.label("pruned"),
            )
            .join(Company, Company.id == JobPosting.company_id, isouter=True)  # type: ignore[arg-type]
        )
        recent = func.coalesce(JobPosting.posted_at, JobPosting.ingested_at) >= cutoff
        linked = select(JobProfileLink.job_id).where(JobProfileLink.profile_id == profile_id)
        acted: set[int] = set()
        if rematch:
            acted = set(
                s.exec(
                    select(Application.job_id).where(
                        Application.profile_id == profile_id,
                        Application.status != ApplicationStatus.archived,
                    )
                ).all()
            )
            # Everything recent and unpruned, plus every posting it already has
            # a verdict on whatever its age or pruning: a blacklist or criteria
            # edit must reach an old job still sitting in the queue.
            stmt = stmt.where(or_(and_(recent, not_(pruned)), JobPosting.id.in_(linked)))  # type: ignore[union-attr]
        else:
            # "Recent, unpruned postings this profile hasn't judged yet."
            stmt = stmt.where(recent, not_(pruned), JobPosting.id.not_in(linked))  # type: ignore[union-attr]
        rows = s.exec(stmt).all()
        current: dict[int, Verdict] = (
            {
                job_id: (status, reason)
                for job_id, status, reason in s.exec(
                    select(
                        JobProfileLink.job_id,
                        JobProfileLink.filter_status,
                        JobProfileLink.filter_reason,
                    ).where(JobProfileLink.profile_id == profile_id)
                ).all()
            }
            if rematch
            else {}
        )

    stats = MatchStats()
    total = len(rows)
    for start in range(0, total, MATCH_BATCH):
        values = []
        for row in rows[start : start + MATCH_BATCH]:
            prior = current.get(row.id, (FilterStatus.dropped, None))
            if not row.pruned:
                status, reason = _verdict(row, cfg, blacklist)
            elif _blacklisted(row, blacklist):
                status, reason = FilterStatus.dropped, BLACKLISTED_REASON
            else:
                status, reason = prior
            if (
                status is FilterStatus.dropped
                and row.id in acted
                and prior[0] is not FilterStatus.dropped
            ):
                stats.kept_acted += 1
                status, reason = prior
            stats.evaluated += 1
            setattr(stats, status.value, getattr(stats, status.value) + 1)
            values.append(
                {
                    "job_id": row.id,
                    "profile_id": profile_id,
                    "filter_status": status,
                    "filter_reason": reason,
                    "linked_at": datetime.now(timezone.utc),
                }
            )
        stmt = sqlite_insert(JobProfileLink).values(values)
        stmt = stmt.on_conflict_do_update(
            index_elements=["job_id", "profile_id"],
            set_={
                "filter_status": stmt.excluded.filter_status,
                "filter_reason": stmt.excluded.filter_reason,
            },
        )
        with _pinned(profile_id) as s:
            if s.get(SearchProfile, profile_id) is None:
                return stats  # deleted mid-run; see above
            s.execute(stmt)
            s.commit()
        if progress_cb is not None:
            progress_cb(min(start + MATCH_BATCH, total), total)
    return stats


MATCH_TASK_KIND = "match"


def start_rematch(profile_id: int) -> str:
    """Re-match one profile in the background: every recent stored posting
    (``MATCH_WINDOW_DAYS``) plus every one it already has a verdict on.

    Called whenever what the profile accepts changes (criteria, home state,
    blacklist) or a profile is created. Local only, no scrape, but ~10 s over a
    full store, so it's a task: progress rides the shared task stream as kind
    ``match`` (ref = profile id), and the layout refreshes the queue when it
    settles. Runs in the network lane, so it queues behind an in-flight scrape
    rather than racing its writes.
    """

    def _run(state: tasks.TaskState) -> None:
        def _progress(done: int, total: int) -> None:
            state.total = total
            state.done = done
            state.publish()

        stats = match_profile(profile_id, rematch=True, progress_cb=_progress)
        state.results.append(
            f"{stats.passed} in your queue, {stats.manual} to review, "
            f"{stats.dropped} filtered out"
        )

    return tasks.start_task(
        MATCH_TASK_KIND, 0, _run, ref=str(profile_id), profile_id=profile_id
    )


def match_all_profiles(
    progress_cb: Callable[[str, MatchStats], None] | None = None,
) -> dict[int, MatchStats]:
    """Give every profile its verdicts on postings it hasn't judged yet — the
    tail of a scrape, so one scrape fills every profile's queue."""
    with Session(engine()) as s:
        profiles = s.exec(select(SearchProfile.id, SearchProfile.name).order_by(SearchProfile.id)).all()
    out: dict[int, MatchStats] = {}
    for pid, name in profiles:
        try:
            out[pid] = match_profile(pid)
        except Exception:  # noqa: BLE001 - one profile's failure must not cost the others their verdicts or fail a committed scrape; its postings stay unlinked, so the next run retries them
            log.exception("matching failed for profile %s (%s)", pid, name)
            continue
        if progress_cb is not None:
            progress_cb(name, out[pid])
    return out
