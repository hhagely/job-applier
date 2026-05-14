from datetime import datetime, timezone
from typing import Optional

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse
from sqlmodel import Session, select

from job_applier import drafts, resume_io
from job_applier.api.schemas import (
    ApplicationOut,
    BulkStatusUpdate,
    CompanyOut,
    DraftIn,
    DraftOut,
    JobDetail,
    JobOut,
    NotesUpdate,
    PendingMatchJob,
    ResumeOut,
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
    MatchScoreHistory,
    Resume,
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


def _score_out(
    s: Optional[MatchScore | MatchScoreHistory],
    *,
    resume_filename: Optional[str] = None,
) -> Optional[ScoreOut]:
    if s is None:
        return None
    return ScoreOut(
        score=s.score, rubric=s.rubric, reasoning=s.reasoning,
        scored_by=s.scored_by, scored_at=s.scored_at,
        resume_id=s.resume_id, resume_filename=resume_filename,
    )


def _application_out(a: Optional[Application]) -> Optional[ApplicationOut]:
    if a is None:
        return None
    return ApplicationOut(
        status=a.status, notes=a.notes, applied_at=a.applied_at, updated_at=a.updated_at,
    )


def _resume_filename_map(session: Session) -> dict[int, str]:
    rows = session.exec(select(Resume.id, Resume.original_filename)).all()
    return {rid: name for rid, name in rows}


def _job_summary(j: JobPosting, resume_names: dict[int, str]) -> JobOut:
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
        company=_company_out(j.company),
        score=_score_out(j.score, resume_filename=score_filename),
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

    resume_names = _resume_filename_map(session)
    return [_job_summary(j, resume_names) for j in jobs]


@app.get("/api/jobs/{job_id}", response_model=JobDetail)
def get_job(job_id: int, session: Session = Depends(get_session)):
    job = session.get(JobPosting, job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    summary = _job_summary(job, _resume_filename_map(session))
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


@app.post("/api/jobs/bulk-status", response_model=list[ApplicationOut])
def set_status_bulk(body: BulkStatusUpdate, session: Session = Depends(get_session)):
    if not body.job_ids:
        raise HTTPException(422, "job_ids must not be empty")
    now = datetime.now(timezone.utc)
    results: list[ApplicationOut] = []
    for job_id in body.job_ids:
        job = session.get(JobPosting, job_id)
        if job is None:
            raise HTTPException(404, f"job {job_id} not found")
        app_row = job.application or Application(job_id=job_id)
        app_row.status = body.status
        if body.status == ApplicationStatus.applied and app_row.applied_at is None:
            app_row.applied_at = now
        app_row.updated_at = now
        session.add(app_row)
        results.append(app_row)
    session.commit()
    return [_application_out(a) for a in results]


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
            )
        )

    active_resume = session.exec(
        select(Resume).where(Resume.is_active == True)  # noqa: E712
    ).first()

    score = existing or MatchScore(job_id=job_id)
    score.score = body.score
    score.rubric = body.rubric
    score.reasoning = body.reasoning
    score.scored_by = body.scored_by
    score.scored_at = datetime.now(timezone.utc)
    score.resume_id = active_resume.id if active_resume else None
    session.add(score)
    session.commit()
    session.refresh(score)
    resume_filename = active_resume.original_filename if active_resume else None
    return _score_out(score, resume_filename=resume_filename)


@app.get("/api/jobs/{job_id}/score-history", response_model=list[ScoreOut])
def list_score_history(job_id: int, session: Session = Depends(get_session)):
    if session.get(JobPosting, job_id) is None:
        raise HTTPException(404, "job not found")
    rows = session.exec(
        select(MatchScoreHistory)
        .where(MatchScoreHistory.job_id == job_id)
        .order_by(MatchScoreHistory.scored_at.desc())
    ).all()
    resume_names = _resume_filename_map(session)
    return [
        _score_out(
            r,
            resume_filename=(
                resume_names.get(r.resume_id) if r.resume_id is not None else None
            ),
        )
        for r in rows
    ]


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


def _resume_out(r: Resume) -> ResumeOut:
    return ResumeOut(
        id=r.id,
        original_filename=r.original_filename,
        page_count=r.page_count,
        is_active=r.is_active,
        uploaded_at=r.uploaded_at,
        extracted_text=r.extracted_text,
    )


def _active_resume(session: Session) -> Resume:
    r = session.exec(select(Resume).where(Resume.is_active == True)).first()  # noqa: E712
    if r is None:
        raise HTTPException(404, "no active resume — POST /api/resume to upload")
    return r


@app.post("/api/resume", response_model=ResumeOut, status_code=201)
async def upload_resume(
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
):
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(415, "PDF required (.pdf extension)")
    pdf_bytes = await file.read()
    if len(pdf_bytes) == 0:
        raise HTTPException(400, "empty file")
    if len(pdf_bytes) > settings.max_resume_bytes:
        raise HTTPException(
            413,
            f"file too large (max {settings.max_resume_bytes // (1024 * 1024)} MiB)",
        )

    try:
        text, page_count = resume_io.extract_text(pdf_bytes)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e

    if not text.strip():
        raise HTTPException(422, "no text extracted — is the PDF image-only?")

    pdf_path = resume_io.save_pdf(pdf_bytes, file.filename)

    # Demote any previously-active resumes (history is preserved).
    for r in session.exec(select(Resume).where(Resume.is_active == True)).all():  # noqa: E712
        r.is_active = False
        session.add(r)

    resume = Resume(
        original_filename=file.filename,
        pdf_path=str(pdf_path),
        extracted_text=text,
        page_count=page_count,
        is_active=True,
    )
    session.add(resume)
    session.commit()
    session.refresh(resume)
    return _resume_out(resume)


@app.get("/api/resume/current", response_model=ResumeOut)
def get_current_resume(session: Session = Depends(get_session)):
    return _resume_out(_active_resume(session))


@app.get("/api/resume/current/pdf")
def download_current_resume(session: Session = Depends(get_session)):
    r = _active_resume(session)
    return FileResponse(
        r.pdf_path,
        media_type="application/pdf",
        filename=r.original_filename,
    )


@app.get("/api/resume/current/markdown", response_class=PlainTextResponse)
def get_current_resume_markdown(session: Session = Depends(get_session)):
    r = _active_resume(session)
    return resume_io.to_markdown(r.extracted_text)


def _draft_out(
    job_id: int, *, include_markdown: bool = False
) -> DraftOut:
    s = drafts.get_status(job_id)
    return DraftOut(
        job_id=s.job_id,
        has_resume_md=s.has_resume_md,
        has_resume_pdf=s.has_resume_pdf,
        has_cover_letter_md=s.has_cover_letter_md,
        has_cover_letter_pdf=s.has_cover_letter_pdf,
        updated_at=s.updated_at,
        resume_md=drafts.read_markdown(job_id, "resume") if include_markdown else None,
        cover_letter_md=(
            drafts.read_markdown(job_id, "cover_letter") if include_markdown else None
        ),
    )


@app.get("/api/jobs/{job_id}/draft", response_model=DraftOut)
def get_draft(
    job_id: int,
    include_markdown: bool = False,
    session: Session = Depends(get_session),
):
    if session.get(JobPosting, job_id) is None:
        raise HTTPException(404, "job not found")
    return _draft_out(job_id, include_markdown=include_markdown)


@app.post("/api/jobs/{job_id}/draft", response_model=DraftOut)
def save_draft(
    job_id: int, body: DraftIn, session: Session = Depends(get_session)
):
    if session.get(JobPosting, job_id) is None:
        raise HTTPException(404, "job not found")
    if body.resume_md is None and body.cover_letter_md is None:
        raise HTTPException(422, "provide at least one of resume_md, cover_letter_md")
    drafts.save_and_render(job_id, body.resume_md, body.cover_letter_md)
    return _draft_out(job_id)


@app.post("/api/jobs/{job_id}/draft/render", response_model=DraftOut)
def render_draft(job_id: int, session: Session = Depends(get_session)):
    if session.get(JobPosting, job_id) is None:
        raise HTTPException(404, "job not found")
    s = drafts.get_status(job_id)
    if not (s.has_resume_md or s.has_cover_letter_md):
        raise HTTPException(404, "no draft markdown to render")
    drafts.render_existing(job_id)
    return _draft_out(job_id)


@app.get("/api/jobs/{job_id}/draft/resume.pdf")
def download_draft_resume(job_id: int, session: Session = Depends(get_session)):
    if session.get(JobPosting, job_id) is None:
        raise HTTPException(404, "job not found")
    path = drafts.pdf_path(job_id, "resume")
    if not path.exists():
        raise HTTPException(404, "tailored resume PDF not found — run /draft first")
    return FileResponse(path, media_type="application/pdf", filename=f"resume-{job_id}.pdf")


@app.get("/api/jobs/{job_id}/draft/cover-letter.pdf")
def download_draft_cover_letter(job_id: int, session: Session = Depends(get_session)):
    if session.get(JobPosting, job_id) is None:
        raise HTTPException(404, "job not found")
    path = drafts.pdf_path(job_id, "cover_letter")
    if not path.exists():
        raise HTTPException(404, "cover letter PDF not found — run /draft first")
    return FileResponse(
        path, media_type="application/pdf", filename=f"cover-letter-{job_id}.pdf"
    )


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}
