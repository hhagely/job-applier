from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse
from sqlmodel import Session, select

from job_applier import drafts, pdf, resume_io
from job_applier.pdf import PdfRendererUnavailable
from job_applier.api.schemas import (
    ApplicationOut,
    BulkStatusUpdate,
    BulkUnemploymentUpdate,
    CompanyOut,
    DraftIn,
    DraftOut,
    FollowupUpdate,
    JobDetail,
    JobOut,
    NotesUpdate,
    PendingMatchJob,
    ResumeOut,
    ScoreIn,
    ScoreOut,
    SearchProfileBody,
    SearchProfileOut,
    SearchProfileRecommendationIn,
    StatusUpdate,
    UnemploymentUpdate,
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
    SearchProfile,
    create_db_and_tables,
    get_session,
)

app = FastAPI(title="job-applier API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.web_origin],
    # The dev launcher / packaged shell picks free loopback ports at boot, so the
    # SvelteKit server's origin is not known ahead of time. Allow any localhost /
    # 127.0.0.1 port in addition to the statically configured web_origin.
    allow_origin_regex=r"^http://(127\.0\.0\.1|localhost)(:\d+)?$",
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


def _application_out(a: Optional[Application]) -> Optional[ApplicationOut]:
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


def _resume_filename_map(session: Session) -> dict[int, str]:
    rows = session.exec(select(Resume.id, Resume.original_filename)).all()
    return {rid: name for rid, name in rows}


def _active_resume_id(session: Session) -> Optional[int]:
    return session.exec(
        select(Resume.id).where(Resume.is_active == True)  # noqa: E712
    ).first()


def _job_summary(
    j: JobPosting,
    resume_names: dict[int, str],
    active_resume_id: Optional[int],
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
        company=_company_out(j.company),
        score=_score_out(
            j.score,
            resume_filename=score_filename,
            active_resume_id=active_resume_id,
        ),
        application=_application_out(j.application),
        duplicate_of=j.duplicate_of,
    )


@app.get("/api/jobs", response_model=list[JobOut])
def list_jobs(
    status: Optional[ApplicationStatus] = None,
    filter_status: Optional[FilterStatus] = FilterStatus.passed,
    min_score: Optional[int] = None,
    unscored_only: bool = False,
    include_duplicates: bool = False,
    limit: int = 100,
    offset: int = 0,
    session: Session = Depends(get_session),
):
    stmt = select(JobPosting)
    if filter_status is not None:
        stmt = stmt.where(JobPosting.filter_status == filter_status)
    if not include_duplicates:
        stmt = stmt.where(JobPosting.duplicate_of.is_(None))  # type: ignore[union-attr]
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
    active_id = _active_resume_id(session)
    return [_job_summary(j, resume_names, active_id) for j in jobs]


@app.get("/api/jobs/{job_id}", response_model=JobDetail)
def get_job(job_id: int, session: Session = Depends(get_session)):
    job = session.get(JobPosting, job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    summary = _job_summary(
        job, _resume_filename_map(session), _active_resume_id(session)
    )
    return JobDetail(**summary.model_dump(), description=job.description)


def _apply_status_transition(
    app_row: Application,
    *,
    new_status: ApplicationStatus,
    now: datetime,
    next_followup_at: Optional[datetime],
    last_contact_at: Optional[datetime],
    outcome: Optional[str],
) -> None:
    """Mutate ``app_row`` for a status change, defaulting the follow-up date.

    When transitioning into ``applied`` and the client didn't supply a
    ``next_followup_at``, fall back to ``applied_at + followup_default_days``.
    """
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


@app.patch("/api/jobs/{job_id}/status", response_model=ApplicationOut)
def set_status(job_id: int, body: StatusUpdate, session: Session = Depends(get_session)):
    job = session.get(JobPosting, job_id)
    if job is None:
        raise HTTPException(404, "job not found")

    app_row = job.application or Application(job_id=job_id)
    if body.notes is not None:
        app_row.notes = body.notes
    _apply_status_transition(
        app_row,
        new_status=body.status,
        now=datetime.now(timezone.utc),
        next_followup_at=body.next_followup_at,
        last_contact_at=body.last_contact_at,
        outcome=body.outcome,
    )
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
        _apply_status_transition(
            app_row,
            new_status=body.status,
            now=now,
            next_followup_at=body.next_followup_at,
            last_contact_at=body.last_contact_at,
            outcome=body.outcome,
        )
        session.add(app_row)
        results.append(app_row)
    session.commit()
    return [_application_out(a) for a in results]


FOLLOWUP_ACTIVE_STATUSES = (
    ApplicationStatus.applied,
    ApplicationStatus.screening,
    ApplicationStatus.interviewing,
)


@app.get("/api/followups", response_model=list[JobOut])
def list_followups(session: Session = Depends(get_session)):
    """Applications past their follow-up date without an outcome recorded yet.

    Covers any status where the user is still expecting to hear back —
    ``applied``, ``screening``, ``interviewing``. Ordered most-overdue first.
    """
    now = datetime.now(timezone.utc)
    stmt = (
        select(JobPosting)
        .join(Application, Application.job_id == JobPosting.id)
        .where(Application.status.in_(FOLLOWUP_ACTIVE_STATUSES))
        .where(Application.outcome.is_(None))
        .where(Application.next_followup_at.is_not(None))
        .where(Application.next_followup_at <= now)
    )
    jobs = list(session.exec(stmt).all())
    jobs.sort(key=lambda j: j.application.next_followup_at)
    resume_names = _resume_filename_map(session)
    active_id = _active_resume_id(session)
    return [_job_summary(j, resume_names, active_id) for j in jobs]


@app.post("/api/jobs/{job_id}/followup", response_model=ApplicationOut)
def set_followup(
    job_id: int, body: FollowupUpdate, session: Session = Depends(get_session)
):
    job = session.get(JobPosting, job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    app_row = job.application
    if app_row is None:
        raise HTTPException(
            409, "no application row yet — set a status before recording follow-ups"
        )
    if body.next_followup_at is not None:
        app_row.next_followup_at = body.next_followup_at
    if body.last_contact_at is not None:
        app_row.last_contact_at = body.last_contact_at
    if body.outcome is not None:
        app_row.outcome = body.outcome
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


def _mark_unemployment(job: JobPosting, *, used: bool, now: datetime) -> Application:
    """Set the unemployment flag on a job's application, creating the row if needed.

    Creates an application row if one doesn't exist yet so a job can be flagged
    before it moves through the pipeline. The timestamp records when it was
    marked and is cleared when unmarked.
    """
    app_row = job.application or Application(
        job_id=job.id, status=ApplicationStatus.new
    )
    app_row.used_for_unemployment = used
    app_row.used_for_unemployment_at = now if used else None
    app_row.updated_at = now
    return app_row


@app.post("/api/jobs/{job_id}/unemployment", response_model=ApplicationOut)
def set_unemployment(
    job_id: int, body: UnemploymentUpdate, session: Session = Depends(get_session)
):
    """Mark (or unmark) an application as reported for an unemployment claim."""
    job = session.get(JobPosting, job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    app_row = _mark_unemployment(
        job, used=body.used, now=datetime.now(timezone.utc)
    )
    session.add(app_row)
    session.commit()
    session.refresh(app_row)
    return _application_out(app_row)


@app.post("/api/jobs/bulk-unemployment", response_model=list[ApplicationOut])
def set_unemployment_bulk(
    body: BulkUnemploymentUpdate, session: Session = Depends(get_session)
):
    if not body.job_ids:
        raise HTTPException(422, "job_ids must not be empty")
    now = datetime.now(timezone.utc)
    results: list[Application] = []
    for job_id in body.job_ids:
        job = session.get(JobPosting, job_id)
        if job is None:
            raise HTTPException(404, f"job {job_id} not found")
        app_row = _mark_unemployment(job, used=body.used, now=now)
        session.add(app_row)
        results.append(app_row)
    session.commit()
    return [_application_out(a) for a in results]


@app.get("/api/pending-match", response_model=list[PendingMatchJob])
def pending_match(
    limit: int = 25,
    include_stale: bool = False,
    session: Session = Depends(get_session),
):
    """Jobs that passed the hard filter and need scoring.

    Always includes unscored jobs. With ``include_stale=true``, also includes
    jobs whose only score is against a non-active resume — the queue that
    Claude Code reads when refreshing scores after a resume change.
    """
    stmt = (
        select(JobPosting)
        .where(JobPosting.filter_status == FilterStatus.passed)
        .order_by(JobPosting.ingested_at.desc())
    )
    jobs = list(session.exec(stmt).all())
    active_id = _active_resume_id(session) if include_stale else None

    def _needs_scoring(j: JobPosting) -> bool:
        if j.score is None:
            return True
        if include_stale and active_id is not None:
            sid = j.score.resume_id
            return sid is not None and sid != active_id
        return False

    pending = [j for j in jobs if _needs_scoring(j)][:limit]
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


@app.get("/api/scores/stale-count")
def stale_score_count(session: Session = Depends(get_session)) -> dict:
    """Count of baseline scores not against the active resume.

    Returns 0 when there's no active resume — there's nothing to be stale
    against in that case.
    """
    active_id = _active_resume_id(session)
    if active_id is None:
        return {"count": 0}
    count = len(
        session.exec(
            select(MatchScore).where(
                MatchScore.resume_id.is_not(None),  # type: ignore[union-attr]
                MatchScore.resume_id != active_id,
            )
        ).all()
    )
    return {"count": count}


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
                score_kind=existing.score_kind,
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
    score.score_kind = body.score_kind
    # Tailored scores are scored against a per-job draft, not a Resume row.
    score.resume_id = (
        active_resume.id if active_resume and body.score_kind == "baseline" else None
    )
    session.add(score)
    session.commit()
    session.refresh(score)
    resume_filename = (
        active_resume.original_filename
        if active_resume and score.resume_id is not None
        else None
    )
    return _score_out(
        score,
        resume_filename=resume_filename,
        active_resume_id=active_resume.id if active_resume else None,
    )


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


def _profile_out(p: Optional[SearchProfile]) -> SearchProfileOut:
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


def _load_or_create_profile(session: Session) -> SearchProfile:
    p = session.exec(select(SearchProfile).order_by(SearchProfile.id)).first()
    if p is None:
        p = SearchProfile()
        session.add(p)
        session.flush()
    return p


@app.get("/api/search-profile", response_model=SearchProfileOut)
def get_search_profile(session: Session = Depends(get_session)):
    p = session.exec(select(SearchProfile).order_by(SearchProfile.id)).first()
    return _profile_out(p)


@app.put("/api/search-profile", response_model=SearchProfileOut)
def put_search_profile(
    body: SearchProfileBody, session: Session = Depends(get_session)
):
    p = _load_or_create_profile(session)
    p.role_titles = body.role_titles
    p.seniority_terms = body.seniority_terms
    p.required_tech = body.required_tech
    p.excluded_tech = body.excluded_tech
    p.extracted_skills = body.extracted_skills
    p.updated_at = datetime.now(timezone.utc)
    session.add(p)
    session.commit()
    session.refresh(p)
    return _profile_out(p)


@app.post("/api/search-profile/recommendations", response_model=SearchProfileOut)
def post_recommendations(
    body: SearchProfileRecommendationIn, session: Session = Depends(get_session)
):
    """Save an LLM-generated proposal as a draft on the profile.

    Does NOT mutate the active fields — the user reviews + accepts via PUT to
    apply. Overwrites any prior draft.
    """
    p = _load_or_create_profile(session)
    p.recommendations_draft = body.model_dump()
    p.updated_at = datetime.now(timezone.utc)
    session.add(p)
    session.commit()
    session.refresh(p)
    return _profile_out(p)


@app.delete("/api/search-profile/recommendations", response_model=SearchProfileOut)
def clear_recommendations(session: Session = Depends(get_session)):
    p = session.exec(select(SearchProfile).order_by(SearchProfile.id)).first()
    if p is None:
        return _profile_out(None)
    p.recommendations_draft = None
    p.updated_at = datetime.now(timezone.utc)
    session.add(p)
    session.commit()
    session.refresh(p)
    return _profile_out(p)


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


def _print_url(request: Request, job_id: int, kind: drafts.DraftKind) -> str:
    """Absolute loopback URL of the print-HTML endpoint the PDF driver loads."""
    base = str(request.base_url).rstrip("/")
    return f"{base}/api/jobs/{job_id}/draft/{kind}/print.html"


def _render_draft_pdfs(request: Request, job_id: int) -> None:
    """Print a PDF for every draft kind that has markdown on disk.

    The browser engine (headless Chromium here, Electron in the packaged app)
    loads the print-HTML endpoint over loopback and prints it; we persist the
    bytes next to the markdown. Markdown is already saved by the caller, so a
    renderer failure never loses the draft text.
    """
    for kind in drafts.existing_markdown_kinds(job_id):
        pdf_bytes = pdf.render_to_pdf(_print_url(request, job_id, kind))
        drafts.render_pdf(job_id, kind, pdf_bytes)


@app.post("/api/jobs/{job_id}/draft", response_model=DraftOut)
def save_draft(
    job_id: int,
    body: DraftIn,
    request: Request,
    session: Session = Depends(get_session),
):
    if session.get(JobPosting, job_id) is None:
        raise HTTPException(404, "job not found")
    if body.resume_md is None and body.cover_letter_md is None:
        raise HTTPException(422, "provide at least one of resume_md, cover_letter_md")
    drafts.save_markdown(job_id, body.resume_md, body.cover_letter_md)
    try:
        _render_draft_pdfs(request, job_id)
    except PdfRendererUnavailable as exc:
        # Markdown is persisted; only the PDF step failed. Report clearly.
        raise HTTPException(503, str(exc)) from exc
    return _draft_out(job_id)


@app.post("/api/jobs/{job_id}/draft/render", response_model=DraftOut)
def render_draft(
    job_id: int, request: Request, session: Session = Depends(get_session)
):
    if session.get(JobPosting, job_id) is None:
        raise HTTPException(404, "job not found")
    s = drafts.get_status(job_id)
    if not (s.has_resume_md or s.has_cover_letter_md):
        raise HTTPException(404, "no draft markdown to render")
    try:
        _render_draft_pdfs(request, job_id)
    except PdfRendererUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    return _draft_out(job_id)


@app.get("/api/jobs/{job_id}/draft/{kind}/print.html", response_class=HTMLResponse)
def draft_print_html(
    job_id: int, kind: str, session: Session = Depends(get_session)
):
    """Standalone print-ready HTML for a draft kind. The PDF driver / Electron
    loads this and prints it to PDF, so the CSS lives in one place."""
    if kind not in ("resume", "cover_letter"):
        raise HTTPException(404, "unknown draft kind")
    if session.get(JobPosting, job_id) is None:
        raise HTTPException(404, "job not found")
    md = drafts.read_markdown(job_id, kind)  # type: ignore[arg-type]
    if md is None:
        raise HTTPException(404, "draft markdown not found")
    return HTMLResponse(drafts.render_print_html(md, kind))  # type: ignore[arg-type]


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
