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

from sqlalchemy.orm import selectinload
from sqlmodel import Session, select

from job_applier.config import settings
from job_applier.ingest import normalize_company
from job_applier.models.db import (
    Application,
    ApplicationStatus,
    BlacklistedCompany,
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


# ---- pending-match selection ----------------------------------------------


def select_pending_jobs(
    session: Session, *, limit: int = 25, include_stale: bool = False
) -> list[JobPosting]:
    """Jobs that passed the hard filter and need scoring.

    Always includes unscored jobs. With ``include_stale``, also includes jobs
    whose only score is against a non-active resume.
    """
    # Eager-load the relationships the selection + its consumers read per row
    # (score for _needs_scoring; company/application for the pending-match
    # serializer and the scoring loop), avoiding a lazy load per job.
    stmt = (
        select(JobPosting)
        .where(JobPosting.filter_status == FilterStatus.passed)
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


# ---- search profile -------------------------------------------------------


def load_or_create_profile(session: Session) -> SearchProfile:
    p = session.exec(select(SearchProfile).order_by(SearchProfile.id)).first()
    if p is None:
        p = SearchProfile()
        session.add(p)
        session.flush()
    return p


def save_recommendations(session: Session, recommendations: dict) -> SearchProfile:
    """Persist an LLM proposal as a draft on the profile. Never mutates the active
    fields — the user reviews + accepts via PUT to apply. ``recommendations`` is a
    plain dict (the router/flow owns the DTO it was validated from)."""
    p = load_or_create_profile(session)
    p.recommendations_draft = dict(recommendations)
    p.updated_at = datetime.now(timezone.utc)
    session.add(p)
    session.commit()
    session.refresh(p)
    return p


# ---- company blacklist ----------------------------------------------------


class BlacklistNameTooShort(ValueError):
    """A company name that normalizes to fewer than 2 alphanumeric chars — too
    thin to match on reliably at ingest, so we refuse to store it."""


def list_blacklisted_companies(session: Session) -> list[BlacklistedCompany]:
    """Every blacklisted company, ordered case-insensitively by name."""
    return list(
        session.exec(
            select(BlacklistedCompany).order_by(BlacklistedCompany.normalized_name)
        ).all()
    )


def add_blacklisted_company(
    session: Session, name: str, reason: Optional[str] = None
) -> BlacklistedCompany:
    """Add a company to the ingest blacklist. Idempotent on the normalized name.

    Returns the existing row if the company is already blacklisted (under any
    naming variant) so re-adding is a no-op rather than a unique-constraint
    error. Raises ``BlacklistNameTooShort`` when the name is too thin to match.
    """
    display = (name or "").strip()
    normalized = normalize_company(display)
    if len(normalized) < 2:
        raise BlacklistNameTooShort(
            "enter a company name with at least two letters or digits"
        )
    existing = session.exec(
        select(BlacklistedCompany).where(
            BlacklistedCompany.normalized_name == normalized
        )
    ).first()
    if existing is not None:
        return existing
    row = BlacklistedCompany(
        name=display,
        normalized_name=normalized,
        reason=(reason or "").strip() or None,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def remove_blacklisted_company(session: Session, blacklist_id: int) -> bool:
    """Remove a blacklist entry by id. Returns True if a row was deleted."""
    row = session.get(BlacklistedCompany, blacklist_id)
    if row is None:
        return False
    session.delete(row)
    session.commit()
    return True
