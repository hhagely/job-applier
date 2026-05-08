from datetime import datetime, timezone
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlmodel import Session, select

from job_applier.api.schemas import (
    ApplicationOut,
    CompanyOut,
    JobDetail,
    JobOut,
    NotesUpdate,
    PendingMatchJob,
    ScoreIn,
    ScoreOut,
    StatusUpdate,
)
from job_applier.config import settings
from job_applier.models.db import (
    Application,
    ApplicationStatus,
    Company,
    FilterStatus,
    JobPosting,
    MatchScore,
    create_db_and_tables,
    get_session,
)

app = FastAPI(title="job-applier API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.web_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _startup() -> None:
    create_db_and_tables()


def _company_out(c: Optional[Company]) -> Optional[CompanyOut]:
    if c is None:
        return None
    return CompanyOut(id=c.id, name=c.name, domain=c.domain, is_blocked=c.is_blocked, notes=c.notes)


def _score_out(s: Optional[MatchScore]) -> Optional[ScoreOut]:
    if s is None:
        return None
    return ScoreOut(
        score=s.score, rubric=s.rubric, reasoning=s.reasoning,
        scored_by=s.scored_by, scored_at=s.scored_at,
    )


def _application_out(a: Optional[Application]) -> Optional[ApplicationOut]:
    if a is None:
        return None
    return ApplicationOut(
        status=a.status, notes=a.notes, applied_at=a.applied_at, updated_at=a.updated_at,
    )


def _job_summary(j: JobPosting) -> JobOut:
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
        company=_company_out(j.company),
        score=_score_out(j.score),
        application=_application_out(j.application),
    )


@app.get("/api/jobs", response_model=list[JobOut])
def list_jobs(
    status: Optional[ApplicationStatus] = None,
    filter_status: Optional[FilterStatus] = FilterStatus.passed,
    min_score: Optional[int] = None,
    unscored_only: bool = False,
    limit: int = 100,
    offset: int = 0,
    session: Session = Depends(get_session),
):
    stmt = select(JobPosting)
    if filter_status is not None:
        stmt = stmt.where(JobPosting.filter_status == filter_status)
    stmt = stmt.order_by(JobPosting.ingested_at.desc()).offset(offset).limit(limit)
    jobs = list(session.exec(stmt).all())

    # In-Python post-filters that need joined data:
    if status is not None:
        jobs = [j for j in jobs if j.application and j.application.status == status]
    if min_score is not None:
        jobs = [j for j in jobs if j.score and j.score.score >= min_score]
    if unscored_only:
        jobs = [j for j in jobs if j.score is None]

    return [_job_summary(j) for j in jobs]


@app.get("/api/jobs/{job_id}", response_model=JobDetail)
def get_job(job_id: int, session: Session = Depends(get_session)):
    job = session.get(JobPosting, job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    summary = _job_summary(job)
    return JobDetail(**summary.model_dump(), description=job.description)


@app.patch("/api/jobs/{job_id}/status", response_model=ApplicationOut)
def set_status(job_id: int, body: StatusUpdate, session: Session = Depends(get_session)):
    job = session.get(JobPosting, job_id)
    if job is None:
        raise HTTPException(404, "job not found")

    app_row = job.application or Application(job_id=job_id)
    app_row.status = body.status
    if body.notes is not None:
        app_row.notes = body.notes
    if body.status == ApplicationStatus.applied and app_row.applied_at is None:
        app_row.applied_at = datetime.now(timezone.utc)
    app_row.updated_at = datetime.now(timezone.utc)
    session.add(app_row)
    session.commit()
    session.refresh(app_row)
    return _application_out(app_row)


@app.post("/api/jobs/{job_id}/notes", response_model=ApplicationOut)
def set_notes(job_id: int, body: NotesUpdate, session: Session = Depends(get_session)):
    job = session.get(JobPosting, job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    app_row = job.application or Application(job_id=job_id, status=ApplicationStatus.new)
    app_row.notes = body.notes
    app_row.updated_at = datetime.now(timezone.utc)
    session.add(app_row)
    session.commit()
    session.refresh(app_row)
    return _application_out(app_row)


@app.get("/api/pending-match", response_model=list[PendingMatchJob])
def pending_match(limit: int = 25, session: Session = Depends(get_session)):
    """Jobs that passed the hard filter and have no MatchScore yet.

    This is the queue that Claude Code reads when running `/match-pending`.
    """
    stmt = (
        select(JobPosting)
        .where(JobPosting.filter_status == FilterStatus.passed)
        .order_by(JobPosting.ingested_at.desc())
    )
    jobs = list(session.exec(stmt).all())
    pending = [j for j in jobs if j.score is None][:limit]
    return [
        PendingMatchJob(
            id=j.id,
            title=j.title,
            company_name=j.company.name if j.company else "Unknown",
            url=j.url,
            location=j.location,
            description=j.description,
        )
        for j in pending
    ]


@app.post("/api/jobs/{job_id}/score", response_model=ScoreOut)
def upsert_score(job_id: int, body: ScoreIn, session: Session = Depends(get_session)):
    job = session.get(JobPosting, job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    if not 0 <= body.score <= 100:
        raise HTTPException(422, "score must be 0-100")

    score = job.score or MatchScore(job_id=job_id)
    score.score = body.score
    score.rubric = body.rubric
    score.reasoning = body.reasoning
    score.scored_by = body.scored_by
    score.scored_at = datetime.now(timezone.utc)
    session.add(score)
    session.commit()
    session.refresh(score)
    return _score_out(score)


@app.get("/api/companies", response_model=list[CompanyOut])
def list_companies(session: Session = Depends(get_session)):
    companies = session.exec(select(Company).order_by(Company.name)).all()
    return [_company_out(c) for c in companies]


@app.post("/api/companies/{company_id}/block", response_model=CompanyOut)
def block_company(company_id: int, blocked: bool = True, session: Session = Depends(get_session)):
    c = session.get(Company, company_id)
    if c is None:
        raise HTTPException(404, "company not found")
    c.is_blocked = blocked
    session.add(c)
    session.commit()
    session.refresh(c)
    return _company_out(c)


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}
