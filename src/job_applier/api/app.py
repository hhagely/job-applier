from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import selectinload
from sqlmodel import Session, select

from job_applier import __version__, ingest, services
from job_applier.ai import tasks as ai_tasks
from job_applier.api import drafts as drafts_router
from job_applier.api import profile as profile_router
from job_applier.api import resume as resume_router
from job_applier.api.ai import router as ai_router
from job_applier.api.deps import require_job
from job_applier.api.schemas import (
    ApplicationOut,
    BulkStatusUpdate,
    BulkUnemploymentUpdate,
    CompanyOut,
    FollowupUpdate,
    JobDetail,
    JobOut,
    NotesUpdate,
    PendingMatchJob,
    ScoreIn,
    ScoreOut,
    StartTaskOut,
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
    create_db_and_tables,
    get_session,
)
from job_applier.sources.refresh import seed_if_empty
from job_applier.updates import check_for_update

@asynccontextmanager
async def _lifespan(_app: FastAPI):
    create_db_and_tables()
    # Seed the per-company source slugs on first boot. The desktop app and
    # `make api` only ever run the server (never `job-applier init`), so without
    # this a fresh DB — e.g. the packaged app's userData dir — starts with an
    # empty SourceSlug table and ingest silently runs only the config-free
    # aggregators (no Greenhouse/Lever/Oracle/...). Idempotent + per-source, so
    # it's a no-op on an already-populated DB and cheap to run every boot.
    seed_if_empty()
    yield
    # Tear the background worker down on shutdown (e.g. Electron closing the app),
    # cancelling any queued task instead of leaking the thread / subprocess.
    ai_tasks.shutdown()


app = FastAPI(title="job-applier API", lifespan=_lifespan)

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

# AI provider + task endpoints, and the per-concern routers split out of this
# module (resume upload, search profile, tailored drafts).
app.include_router(ai_router)
app.include_router(resume_router.router)
app.include_router(profile_router.router)
app.include_router(drafts_router.router)


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
    # Eager-load the 1:1/n:1 relationships _job_summary reads, so rendering N rows
    # is a constant handful of queries instead of ~3 lazy loads per row (N+1).
    stmt = select(JobPosting).options(
        selectinload(JobPosting.company),
        selectinload(JobPosting.score),
        selectinload(JobPosting.application),
    )
    if filter_status is not None:
        stmt = stmt.where(JobPosting.filter_status == filter_status)
    if not include_duplicates:
        stmt = stmt.where(JobPosting.duplicate_of.is_(None))  # type: ignore[union-attr]
    stmt = stmt.order_by(JobPosting.ingested_at.desc())
    jobs = list(session.exec(stmt).all())

    # In-Python post-filters that need joined data — applied BEFORE pagination so
    # limit/offset count matching rows, not the raw ingest order (e.g.
    # ?status=applied&limit=100 returns 100 applied jobs, not applied-among-newest-100).
    if status is not None:
        jobs = [j for j in jobs if j.application and j.application.status == status]
    if min_score is not None:
        jobs = [j for j in jobs if j.score and j.score.score >= min_score]
    if unscored_only:
        jobs = [j for j in jobs if j.score is None]
    jobs = jobs[offset : offset + limit]

    resume_names = _resume_filename_map(session)
    active_id = _active_resume_id(session)
    return [_job_summary(j, resume_names, active_id) for j in jobs]


@app.get("/api/jobs/{job_id}", response_model=JobDetail)
def get_job(
    job: JobPosting = Depends(require_job), session: Session = Depends(get_session)
):
    summary = _job_summary(
        job, _resume_filename_map(session), _active_resume_id(session)
    )
    return JobDetail(**summary.model_dump(), description=job.description)


# Status-transition logic lives in services (shared with the background scorer's
# auto-archive). Thin wrapper keeps the single-status endpoint's call site tidy.
_apply_status_transition = services.apply_status_transition


@app.patch("/api/jobs/{job_id}/status", response_model=ApplicationOut)
def set_status(
    body: StatusUpdate,
    job: JobPosting = Depends(require_job),
    session: Session = Depends(get_session),
):
    app_row = job.application or Application(job_id=job.id)
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
    try:
        results = services.bulk_set_status(
            session,
            body.job_ids,
            body.status,
            next_followup_at=body.next_followup_at,
            last_contact_at=body.last_contact_at,
            outcome=body.outcome,
        )
    except services.JobNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
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
    body: FollowupUpdate,
    job: JobPosting = Depends(require_job),
    session: Session = Depends(get_session),
):
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
def set_notes(
    body: NotesUpdate,
    job: JobPosting = Depends(require_job),
    session: Session = Depends(get_session),
):
    app_row = job.application or Application(job_id=job.id, status=ApplicationStatus.new)
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
    body: UnemploymentUpdate,
    job: JobPosting = Depends(require_job),
    session: Session = Depends(get_session),
):
    """Mark (or unmark) an application as reported for an unemployment claim."""
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
    jobs whose only score is against a non-active resume — the queue the
    background scorer reads when refreshing scores after a resume change.
    """
    pending = services.select_pending_jobs(
        session, limit=limit, include_stale=include_stale
    )
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
    try:
        score = services.upsert_score(
            session,
            job_id,
            score=body.score,
            rubric=body.rubric,
            reasoning=body.reasoning,
            scored_by=body.scored_by,
            score_kind=body.score_kind,
        )
    except services.JobNotFound:
        raise HTTPException(404, "job not found")
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc

    active_resume = services.active_resume(session)
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
def list_score_history(
    job: JobPosting = Depends(require_job), session: Session = Depends(get_session)
):
    rows = session.exec(
        select(MatchScoreHistory)
        .where(MatchScoreHistory.job_id == job.id)
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


def _run_ingest_task(state: "ai_tasks.TaskState") -> None:
    """Worker body: pull jobs from every source, reporting per-source progress."""

    def _cb(done: int, total: int, name: str, stats: ingest.IngestStats) -> None:
        state.total = total
        state.done = done
        state.results.append(
            f"{name}: {stats.inserted} new / {stats.passed_filter} passed (running total)"
        )
        state.publish()

    stats = ingest.run_ingest(progress_cb=_cb)
    state.results.append(
        f"done: {stats.inserted} new, {stats.passed_filter} passed, {stats.fetched} fetched"
    )


@app.post("/api/ingest", response_model=StartTaskOut)
def start_ingest(session: Session = Depends(get_session)):
    """Kick off a background scrape of every source. Poll GET /api/ai/tasks/{id}
    for per-source progress. Needs no AI provider — just network access."""
    from job_applier.sources import get_all_sources

    total = len(get_all_sources())
    task_id = ai_tasks.start_task("ingest", total, _run_ingest_task)
    return StartTaskOut(task_id=task_id)


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "version": __version__}


@app.get("/api/version")
def version() -> dict:
    """The single source of truth for the app version (Workstream B): the backend
    ``__version__``. The installer filename and the desktop bridge are stamped from
    this same value at build time so all three agree."""
    return {"version": __version__}


@app.get("/api/update")
def update() -> dict:
    """Compare the running version to the latest GitHub Release. Cached + fail-soft;
    returns ``update_available: False`` offline / rate-limited (Workstream E)."""
    return check_for_update()
