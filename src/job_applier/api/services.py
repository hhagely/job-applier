"""Shared persistence/query logic reused by both the HTTP routes and the AI
orchestrator (Phase 4). Keeping the score upsert, pending-match selection, and
bulk-status mutation here means there is exactly one code path for each — the
background scorer and the REST endpoints can't drift.

These functions take an explicit ``Session`` and raise plain exceptions
(``JobNotFound`` / ``ValueError``); the HTTP layer maps those to status codes so
this module stays framework-agnostic (callable from a background thread too).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlmodel import Session, select

from job_applier.api.schemas import (
    ScoreIn,
    SearchProfileOut,
    SearchProfileRecommendationIn,
)
from job_applier.config import settings
from job_applier.models.db import (
    Application,
    ApplicationStatus,
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
    return session.exec(
        select(Resume).where(Resume.is_active == True)  # noqa: E712
    ).first()


# ---- scoring persistence --------------------------------------------------


def upsert_score(session: Session, job_id: int, body: ScoreIn) -> MatchScore:
    """Upsert the active score for a job, snapshotting the prior value to history.

    One code path for the REST endpoint and the background scorer. Baseline
    scores are stamped with the active resume id (so they can go stale); tailored
    scores carry no resume id by design.
    """
    job = session.get(JobPosting, job_id)
    if job is None:
        raise JobNotFound(job_id)
    if not 0 <= body.score <= 100:
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
    score = existing or MatchScore(job_id=job_id)
    score.score = body.score
    score.rubric = body.rubric
    score.reasoning = body.reasoning
    score.scored_by = body.scored_by
    score.scored_at = datetime.now(timezone.utc)
    score.score_kind = body.score_kind
    score.resume_id = resume.id if resume and body.score_kind == "baseline" else None
    session.add(score)
    session.commit()
    session.refresh(score)
    return score


# ---- pending-match selection ----------------------------------------------


def select_pending_jobs(
    session: Session, *, limit: int = 25, include_stale: bool = False
) -> list[JobPosting]:
    """Jobs that passed the hard filter and need scoring.

    Always includes unscored jobs. With ``include_stale``, also includes jobs
    whose only score is against a non-active resume.
    """
    stmt = (
        select(JobPosting)
        .where(JobPosting.filter_status == FilterStatus.passed)
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


# ---- search profile -------------------------------------------------------


def load_or_create_profile(session: Session) -> SearchProfile:
    p = session.exec(select(SearchProfile).order_by(SearchProfile.id)).first()
    if p is None:
        p = SearchProfile()
        session.add(p)
        session.flush()
    return p


def profile_out(p: Optional[SearchProfile]) -> SearchProfileOut:
    if p is None:
        return SearchProfileOut(using_defaults=True)
    using_defaults = not p.required_tech or not p.seniority_terms
    return SearchProfileOut(
        id=p.id,
        role_titles=list(p.role_titles or []),
        seniority_terms=list(p.seniority_terms or []),
        required_tech=list(p.required_tech or []),
        excluded_tech=list(p.excluded_tech or []),
        extracted_skills=list(p.extracted_skills or []),
        recommendations_draft=p.recommendations_draft,
        updated_at=p.updated_at,
        using_defaults=using_defaults,
    )


def save_recommendations(
    session: Session, body: SearchProfileRecommendationIn
) -> SearchProfile:
    """Persist an LLM proposal as a draft on the profile. Never mutates the active
    fields — the user reviews + accepts via PUT to apply."""
    p = load_or_create_profile(session)
    p.recommendations_draft = body.model_dump()
    p.updated_at = datetime.now(timezone.utc)
    session.add(p)
    session.commit()
    session.refresh(p)
    return p
