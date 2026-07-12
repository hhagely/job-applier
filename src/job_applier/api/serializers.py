"""ORM -> API DTO serializers.

Pulled out of ``api/app.py`` so the ORM->schema mapping (a distinct concern from
HTTP routing) lives in one place and can be shared by any router. These build the
``schemas`` DTOs from ``models`` rows; they hold no request/response logic.
"""

from __future__ import annotations

from typing import Optional

from sqlmodel import Session, select

from job_applier.api.schemas import ApplicationOut, CompanyOut, JobOut, ScoreOut
from job_applier.models.db import (
    Application,
    Company,
    JobPosting,
    MatchScore,
    MatchScoreHistory,
    Resume,
)


def company_out(c: Optional[Company]) -> Optional[CompanyOut]:
    if c is None:
        return None
    return CompanyOut(id=c.id, name=c.name, domain=c.domain, is_blocked=c.is_blocked, notes=c.notes)


def score_out(
    s: Optional[MatchScore | MatchScoreHistory],
    *,
    resume_filename: Optional[str] = None,
    active_resume_id: Optional[int] = None,
) -> Optional[ScoreOut]:
    if s is None:
        return None
    # Tailored scores carry no resume_id by design — never stale.
    is_stale = (
        s.resume_id is not None
        and active_resume_id is not None
        and s.resume_id != active_resume_id
    )
    return ScoreOut(
        score=s.score, rubric=s.rubric, reasoning=s.reasoning,
        scored_by=s.scored_by, scored_at=s.scored_at,
        resume_id=s.resume_id, resume_filename=resume_filename,
        score_kind=s.score_kind, is_stale=is_stale,
    )


def application_out(a: Optional[Application]) -> Optional[ApplicationOut]:
    if a is None:
        return None
    return ApplicationOut(
        status=a.status,
        notes=a.notes,
        applied_at=a.applied_at,
        updated_at=a.updated_at,
        next_followup_at=a.next_followup_at,
        last_contact_at=a.last_contact_at,
        outcome=a.outcome,
        used_for_unemployment=a.used_for_unemployment,
        used_for_unemployment_at=a.used_for_unemployment_at,
    )


def resume_filename_map(session: Session) -> dict[int, str]:
    rows = session.exec(select(Resume.id, Resume.original_filename)).all()
    return {rid: name for rid, name in rows}


def active_resume_id(session: Session) -> Optional[int]:
    return session.exec(
        select(Resume.id).where(Resume.is_active == True)  # noqa: E712
    ).first()


def job_summary(
    j: JobPosting,
    resume_names: dict[int, str],
    active_resume_id_value: Optional[int],
) -> JobOut:
    score_filename = (
        resume_names.get(j.score.resume_id)
        if j.score and j.score.resume_id is not None
        else None
    )
    return JobOut(
        id=j.id,
        source=j.source,
        url=j.url,
        title=j.title,
        location=j.location,
        remote=j.remote,
        employment_type=j.employment_type,
        posted_at=j.posted_at,
        ingested_at=j.ingested_at,
        filter_status=j.filter_status,
        filter_reason=j.filter_reason,
        company=company_out(j.company),
        score=score_out(
            j.score,
            resume_filename=score_filename,
            active_resume_id=active_resume_id_value,
        ),
        application=application_out(j.application),
        duplicate_of=j.duplicate_of,
    )
