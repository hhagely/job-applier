"""Shared persistence/query logic reused by both the HTTP routes and the AI
orchestrator (Phase 4). Keeping the score upsert, pending-match selection, and
bulk-status mutation here means there is exactly one code path for each — the
background scorer and the REST endpoints can't drift.

These functions take an explicit ``Session``, accept primitives, and return ORM
rows (or raise plain exceptions ``JobNotFound`` / ``ValueError``); the HTTP layer
owns the request/response DTOs and maps those exceptions to status codes. Keeping
this module free of ``job_applier.api`` imports is deliberate: the application
layer must not depend on the web edge, so a background thread (or a second entry
point) can call it without dragging in FastAPI.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func, or_
from sqlalchemy.orm import selectinload
from sqlmodel import Session, select
from sqlmodel.sql.expression import SelectOfScalar

from job_applier import profiles
from job_applier.config import settings
from job_applier.models.db import (
    Application,
    ApplicationStatus,
    Company,
    FilterStatus,
    JobPosting,
    MatchScore,
    MatchScoreHistory,
    Resume,
    SearchProfile,
)


class JobNotFound(Exception):
    """Raised when a job id doesn't resolve to a row."""

    def __init__(self, job_id: int) -> None:
        super().__init__(f"job {job_id} not found")
        self.job_id = job_id


def active_resume(session: Session) -> Optional[Resume]:
    """The resume this session's profile scores and drafts with (see
    ``profiles.active_resume``)."""
    return profiles.active_resume(session)


# ---- scoring persistence --------------------------------------------------


def upsert_score(
    session: Session,
    job_id: int,
    *,
    score: int,
    rubric: Optional[dict] = None,
    reasoning: Optional[str] = None,
    scored_by: str = "claude-code",
    score_kind: str = "baseline",
) -> MatchScore:
    """Upsert the active score for a job, snapshotting the prior value to history.

    One code path for the REST endpoint and the background scorer. Baseline
    scores are stamped with the active resume id (so they can go stale); tailored
    scores carry no resume id by design.
    """
    job = session.get(JobPosting, job_id)
    if job is None:
        raise JobNotFound(job_id)
    if not 0 <= score <= 100:
        raise ValueError("score must be 0-100")

    existing = job.score
    if existing is not None:
        session.add(
            MatchScoreHistory(
                job_id=existing.job_id,
                score=existing.score,
                rubric=existing.rubric,
                reasoning=existing.reasoning,
                scored_by=existing.scored_by,
                scored_at=existing.scored_at,
                resume_id=existing.resume_id,
                score_kind=existing.score_kind,
            )
        )

    resume = active_resume(session)
    row = existing or MatchScore(job_id=job_id)
    row.score = score
    row.rubric = rubric or {}
    row.reasoning = reasoning
    row.scored_by = scored_by
    row.scored_at = datetime.now(timezone.utc)
    row.score_kind = score_kind
    row.resume_id = resume.id if resume and score_kind == "baseline" else None
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def adopt_scores(session: Session, *, resume_id: int) -> int:
    """Re-stamp baseline scores onto ``resume_id`` so they stop reading as stale.

    The escape hatch for a *minor* resume edit. Staleness is only an id mismatch
    (see ``score_out``), and every upload writes a new ``Resume`` row, so fixing a
    typo would otherwise invalidate every score at once and force a full re-run.
    Adopting re-points the active rows instead — no AI calls, one UPDATE.

    Only active ``MatchScore`` rows move; ``MatchScoreHistory`` keeps the resume
    each score was really computed against, so the audit trail stays honest. The
    caller owns the judgment that the edit was small enough to keep the numbers.

    Scoped to the active profile's postings: another profile's scores were
    computed against *its* resume and aren't stale, just not in view — adopting
    them would stamp them with a resume they were never scored against.

    Returns the number of scores adopted.
    """
    rows = session.exec(stale_scores_stmt(session, resume_id)).all()
    for row in rows:
        row.resume_id = resume_id
        session.add(row)
    session.commit()
    return len(rows)


def stale_scores_stmt(session: Session, resume_id: int) -> SelectOfScalar[MatchScore]:
    """Baseline scores on the active profile's postings not against ``resume_id``.

    Shared by adoption and the stale-count endpoint so the two can't disagree
    about what "stale" covers.
    """
    return select(MatchScore).where(
        MatchScore.resume_id.is_not(None),  # type: ignore[union-attr]
        MatchScore.resume_id != resume_id,
        MatchScore.job_id.in_(profiles.queue_job_ids(session)),  # type: ignore[attr-defined]
    )


# ---- the queue -------------------------------------------------------------

# The status facet of a posting nobody has triaged (it has no Application row).
UNTRIAGED = "none"


def status_facet(job: JobPosting) -> str:
    """The queue facet a posting falls under: its ``ApplicationStatus`` value, or
    ``UNTRIAGED``. Takes ``ApplicationStatus(...)`` rather than reading
    ``.value`` so it works whether the ORM handed back the enum or the string."""
    if job.application is None:
        return UNTRIAGED
    return ApplicationStatus(job.application.status).value


def list_queue(
    session: Session,
    *,
    filter_status: Optional[FilterStatus],
    statuses: Optional[set[str]] = None,
    exclude_archived: bool = False,
    min_score: Optional[int] = None,
    unscored_only: bool = False,
    include_duplicates: bool = False,
    limit: int = 100,
    offset: int = 0,
) -> list[JobPosting]:
    """One page of the session profile's queue, newest first.

    ``filter_status`` is the profile's verdict (``None`` means passed or
    manual). ``statuses`` is a set of facets (``status_facet``); an explicit
    selection always wins over ``exclude_archived``, since asking for archived
    and getting nothing back would be a trap. ``exclude_archived`` is what keeps
    the default queue from spending its whole limit on auto-archived low scorers.
    """
    # Eager-load the relationships the summary serializer reads, so rendering N
    # rows is a constant handful of queries instead of ~3 lazy loads per row.
    stmt = select(JobPosting).options(
        selectinload(JobPosting.company),
        selectinload(JobPosting.score),
        selectinload(JobPosting.application),
        selectinload(JobPosting.link),
    )
    stmt = stmt.where(JobPosting.id.in_(profiles.queue_job_ids(session, filter_status)))  # type: ignore[union-attr]
    if not include_duplicates:
        stmt = stmt.where(JobPosting.duplicate_of.is_(None))  # type: ignore[union-attr]
    stmt = stmt.order_by(JobPosting.ingested_at.desc())
    jobs = list(session.exec(stmt).all())

    # Post-filters on joined data run BEFORE pagination, so limit/offset count
    # matching rows (?status=applied&limit=100 returns 100 applied jobs, not the
    # applied ones among the newest 100).
    if statuses:
        jobs = [j for j in jobs if status_facet(j) in statuses]
    elif exclude_archived:
        jobs = [j for j in jobs if status_facet(j) != ApplicationStatus.archived.value]
    if min_score is not None:
        jobs = [j for j in jobs if j.score and j.score.score >= min_score]
    if unscored_only:
        jobs = [j for j in jobs if j.score is None]
    return jobs[offset : offset + limit]


def queue_status_counts(
    session: Session,
    *,
    filter_status: Optional[FilterStatus],
    include_duplicates: bool = False,
) -> dict[str, int]:
    """How many of the session profile's queue postings sit in each facet
    (every ``ApplicationStatus`` value plus ``UNTRIAGED``, zeros included). One
    GROUP BY rather than loading the queue to count it."""
    stmt = (
        select(Application.status, func.count(JobPosting.id))
        .select_from(JobPosting)
        .outerjoin(Application, Application.job_id == JobPosting.id)  # type: ignore[arg-type]
        .group_by(Application.status)  # type: ignore[arg-type]
    )
    stmt = stmt.where(JobPosting.id.in_(profiles.queue_job_ids(session, filter_status)))  # type: ignore[union-attr]
    if not include_duplicates:
        stmt = stmt.where(JobPosting.duplicate_of.is_(None))  # type: ignore[union-attr]
    counts = {UNTRIAGED: 0, **{s.value: 0 for s in ApplicationStatus}}
    for raw_status, n in session.exec(stmt).all():  # type: ignore[call-overload]
        # LEFT JOIN misses (never-triaged postings) come back with a NULL status.
        key = UNTRIAGED if raw_status is None else ApplicationStatus(raw_status).value
        counts[key] += n
    return counts


# ---- pending-match selection ----------------------------------------------


def select_pending_jobs(
    session: Session, *, limit: int = 25, include_stale: bool = False
) -> list[JobPosting]:
    """Jobs that passed the hard filter and need scoring.

    Always includes unscored jobs. With ``include_stale``, also includes jobs
    whose only score is against a non-active resume. Limited to the active
    profile's postings, so a re-score never runs one profile's jobs against
    another profile's resume.
    """
    # Eager-load the relationships the selection + its consumers read per row
    # (score for _needs_scoring; company/application for the pending-match
    # serializer and the scoring loop), avoiding a lazy load per job.
    stmt = (
        select(JobPosting)
        .where(JobPosting.id.in_(profiles.queue_job_ids(session, FilterStatus.passed)))  # type: ignore[union-attr]
        .options(
            selectinload(JobPosting.company),
            selectinload(JobPosting.score),
            selectinload(JobPosting.application),
        )
        .order_by(JobPosting.ingested_at.desc())
    )
    jobs = list(session.exec(stmt).all())
    active_id = active_resume(session).id if include_stale and active_resume(session) else None

    def _needs_scoring(j: JobPosting) -> bool:
        if j.score is None:
            return True
        if include_stale and active_id is not None:
            sid = j.score.resume_id
            return sid is not None and sid != active_id
        return False

    return [j for j in jobs if _needs_scoring(j)][:limit]


# ---- bulk status ----------------------------------------------------------


def apply_status_transition(
    app_row: Application,
    *,
    new_status: ApplicationStatus,
    now: datetime,
    next_followup_at: Optional[datetime] = None,
    last_contact_at: Optional[datetime] = None,
    outcome: Optional[str] = None,
) -> None:
    """Mutate ``app_row`` for a status change, defaulting the follow-up date when
    transitioning into ``applied``."""
    app_row.status = new_status
    if new_status == ApplicationStatus.applied and app_row.applied_at is None:
        app_row.applied_at = now
    if next_followup_at is not None:
        app_row.next_followup_at = next_followup_at
    elif (
        new_status == ApplicationStatus.applied
        and app_row.next_followup_at is None
        and app_row.applied_at is not None
    ):
        app_row.next_followup_at = app_row.applied_at + timedelta(
            days=settings.followup_default_days
        )
    if last_contact_at is not None:
        app_row.last_contact_at = last_contact_at
    if outcome is not None:
        app_row.outcome = outcome
    app_row.updated_at = now


def bulk_set_status(
    session: Session,
    job_ids: list[int],
    status: ApplicationStatus,
    *,
    next_followup_at: Optional[datetime] = None,
    last_contact_at: Optional[datetime] = None,
    outcome: Optional[str] = None,
) -> list[Application]:
    """Set status on many jobs in one commit. Raises ``JobNotFound`` on any bad id."""
    now = datetime.now(timezone.utc)
    results: list[Application] = []
    for job_id in job_ids:
        job = session.get(JobPosting, job_id)
        if job is None:
            raise JobNotFound(job_id)
        app_row = job.application or Application(job_id=job_id)
        apply_status_transition(
            app_row,
            new_status=status,
            now=now,
            next_followup_at=next_followup_at,
            last_contact_at=last_contact_at,
            outcome=outcome,
        )
        session.add(app_row)
        results.append(app_row)
    session.commit()
    return results


# ---- posting search -------------------------------------------------------

#: Below this a substring match is too noisy to be useful (and "a" would scan
#: the whole table for nothing).
SEARCH_MIN_TERM = 2


def _like_contains(term: str) -> str:
    """A LIKE pattern matching ``term`` anywhere, with wildcards escaped so a
    query like "50%" or "back_end" is taken literally."""
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def search_jobs(session: Session, query: str, *, limit: int = 20) -> list[JobPosting]:
    """Ingested postings whose title or company name contains ``query``.

    Deliberately wider than the queue view: it spans every persisted posting
    (passed *and* manual, archived included) because the point is to find a job
    you know was ingested, not to browse the current queue. Hidden duplicates are
    skipped so one role doesn't fill the list with its cross-source twins.

    Results are ranked exact match -> prefix match -> substring; the SQL ordering
    is recency and Python's sort is stable, so newest wins inside each band.
    """
    term = query.strip()
    if len(term) < SEARCH_MIN_TERM:
        return []
    pattern = _like_contains(term)
    stmt = (
        select(JobPosting)
        .join(Company, isouter=True)
        .where(
            JobPosting.duplicate_of.is_(None),  # type: ignore[union-attr]
            or_(
                JobPosting.title.ilike(pattern, escape="\\"),  # type: ignore[attr-defined]
                Company.name.ilike(pattern, escape="\\"),  # type: ignore[attr-defined]
            ),
        )
        .options(
            selectinload(JobPosting.company),
            selectinload(JobPosting.score),
            selectinload(JobPosting.application),
        )
        .order_by(JobPosting.ingested_at.desc())  # type: ignore[union-attr]
    )
    jobs = list(session.exec(stmt).all())

    needle = term.lower()

    def rank(j: JobPosting) -> int:
        fields = [j.title.lower(), (j.company.name if j.company else "").lower()]
        if any(f == needle for f in fields):
            return 0
        if any(f.startswith(needle) for f in fields):
            return 1
        return 2

    jobs.sort(key=rank)
    return jobs[:limit]


# ---- search profile -------------------------------------------------------


load_or_create_profile = profiles.load_or_create_profile


def save_recommendations(session: Session, recommendations: dict) -> SearchProfile:
    """Persist an LLM proposal as a draft on the active profile. Never mutates the active
    fields — the user reviews + accepts via PUT to apply. ``recommendations`` is a
    plain dict (the router/flow owns the DTO it was validated from)."""
    p = load_or_create_profile(session)
    p.recommendations_draft = dict(recommendations)
    p.updated_at = datetime.now(timezone.utc)
    session.add(p)
    session.commit()
    session.refresh(p)
    return p
