from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError
from sqlmodel import Session, select

from job_applier import __version__, profiles, services
from job_applier.ai import tasks as ai_tasks
from job_applier.api import blacklist as blacklist_router
from job_applier.api import drafts as drafts_router
from job_applier.api import preferences as preferences_router
from job_applier.api import profile as profile_router
from job_applier.api import resume as resume_router
from job_applier.api import scrape as scrape_router
from job_applier.api import watchlist as watchlist_router
from job_applier.api.ai import router as ai_router
from job_applier.api.deps import require_job
from job_applier.api.serializers import active_resume_id as _active_resume_id
from job_applier.api.serializers import application_out as _application_out
from job_applier.api.serializers import company_out as _company_out
from job_applier.api.serializers import job_summary as _job_summary
from job_applier.api.serializers import resume_filename_map as _resume_filename_map
from job_applier.api.serializers import score_out as _score_out
from job_applier.api.schemas import (
    ApplicationOut,
    BulkStatusUpdate,
    BulkUnemploymentUpdate,
    CompanyOut,
    FollowupUpdate,
    JobDetail,
    OtherProfileStatus,
    JobOut,
    NotesUpdate,
    PendingMatchJob,
    ScoreIn,
    ScoreOut,
    StatusCountsOut,
    StatusFacet,
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
    MatchScoreHistory,
    create_db_and_tables,
    get_session,
)
from job_applier.sources.refresh import seed_if_empty
from job_applier.updates import check_for_update

@asynccontextmanager
async def _lifespan(_app: FastAPI):
    create_db_and_tables()
    # Drafts became per-profile; existing ones belong to the profile that owned
    # them (the migrated Default). Filesystem, so not in create_db_and_tables.
    profiles.adopt_legacy_drafts()
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

@app.exception_handler(OperationalError)
async def _database_locked(_request: Request, exc: OperationalError):
    """Turn a lost race for SQLite's write lock into an actionable 503.

    SQLite allows exactly one writer at a time. Nothing here should hold the lock
    long enough for a UI mutation to exhaust ``busy_timeout`` — ingest writes in
    short batches with no transaction open across its network I/O, precisely so
    it doesn't — but this is the backstop: if some future long-running writer
    reintroduces the problem, the user gets "try again" rather than an opaque 500,
    and the cause is named in the response instead of only in a traceback.

    Matches on ``is locked`` because SQLite has two busy errors with two different
    messages: ``SQLITE_BUSY`` -> "database is locked" and ``SQLITE_LOCKED`` ->
    "database table is locked". Anything else (a missing table, a bad column) is a
    real bug and is re-raised so it stays a 500 rather than a misleading "retry".
    """
    if "is locked" not in str(exc).lower():
        raise exc
    return JSONResponse(
        status_code=503,
        headers={"Retry-After": "2"},
        content={
            "detail": "The database is busy with a background job. Please try again."
        },
    )


# AI provider + task endpoints, and the per-concern routers split out of this
# module (resume upload, search profile, tailored drafts).
app.include_router(ai_router)
app.include_router(resume_router.router)
app.include_router(profile_router.router)
app.include_router(drafts_router.router)
app.include_router(blacklist_router.router)
app.include_router(watchlist_router.router)
app.include_router(preferences_router.router)
app.include_router(scrape_router.router)


@app.get("/api/jobs", response_model=list[JobOut])
def list_jobs(
    status: Optional[list[StatusFacet]] = Query(None),
    filter_status: Optional[FilterStatus] = FilterStatus.passed,
    min_score: Optional[int] = None,
    unscored_only: bool = False,
    include_duplicates: bool = False,
    exclude_archived: bool = False,
    limit: int = 100,
    offset: int = 0,
    session: Session = Depends(get_session),
) -> list[JobOut]:
    """The active profile's queue: postings its rules passed (``filter_status``
    is *its* verdict; ``None`` means passed or manual). ``status`` is a
    multi-select of facets, as the queue's chips are."""
    jobs = services.list_queue(
        session,
        filter_status=filter_status,
        statuses={s.value for s in status} if status else None,
        exclude_archived=exclude_archived,
        min_score=min_score,
        unscored_only=unscored_only,
        include_duplicates=include_duplicates,
        limit=limit,
        offset=offset,
    )
    resume_names = _resume_filename_map(session)
    active_id = _active_resume_id(session)
    return [_job_summary(j, resume_names, active_id) for j in jobs]


# NOTE: must stay ABOVE /api/jobs/{job_id} — FastAPI matches in declaration order,
# so a route defined after it would be swallowed by the int path param and 422.
@app.get("/api/jobs/status-counts", response_model=StatusCountsOut)
def job_status_counts(
    filter_status: Optional[FilterStatus] = FilterStatus.passed,
    include_duplicates: bool = False,
    session: Session = Depends(get_session),
) -> StatusCountsOut:
    """Per-status totals across the whole queue, for the filter chips.

    Deliberately a separate call rather than a field on /api/jobs: the chips must
    count every matching posting, while /api/jobs returns one limited page.
    """
    raw = services.queue_status_counts(
        session, filter_status=filter_status, include_duplicates=include_duplicates
    )
    counts = {StatusFacet(k): n for k, n in raw.items()}
    return StatusCountsOut(counts=counts, total=sum(counts.values()))


@app.get("/api/search", response_model=list[JobOut])
def search(
    q: str = "",
    limit: int = 20,
    session: Session = Depends(get_session),
) -> list[JobOut]:
    """Free-text lookup over ingested postings by job title or company name.

    Backs the Ctrl/Cmd-K palette, which is why it is a separate endpoint rather
    than a `q` param on /api/jobs: it intentionally ignores the queue's filters
    (archived and manual-review postings are findable too).
    """
    jobs = services.search_jobs(session, q, limit=max(1, min(limit, 50)))
    resume_names = _resume_filename_map(session)
    active_id = _active_resume_id(session)
    return [_job_summary(j, resume_names, active_id) for j in jobs]


@app.get("/api/jobs/{job_id}", response_model=JobDetail)
def get_job(
    job: JobPosting = Depends(require_job), session: Session = Depends(get_session)
) -> JobDetail:
    summary = _job_summary(
        job, _resume_filename_map(session), _active_resume_id(session)
    )
    others = [
        OtherProfileStatus(profile_id=pid, name=name, status=status)
        for pid, name, status in profiles.other_profile_statuses(session, job.id)
    ]
    return JobDetail(**summary.model_dump(), description=job.description, other_profiles=others)


# Status-transition logic lives in services (shared with the background scorer's
# auto-archive). Thin wrapper keeps the single-status endpoint's call site tidy.
_apply_status_transition = services.apply_status_transition


@app.patch("/api/jobs/{job_id}/status", response_model=ApplicationOut)
def set_status(
    body: StatusUpdate,
    job: JobPosting = Depends(require_job),
    session: Session = Depends(get_session),
) -> ApplicationOut:
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
def set_status_bulk(body: BulkStatusUpdate, session: Session = Depends(get_session)) -> list[ApplicationOut]:
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
def list_followups(session: Session = Depends(get_session)) -> list[JobOut]:
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
) -> ApplicationOut:
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
) -> ApplicationOut:
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
) -> ApplicationOut:
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
) -> list[ApplicationOut]:
    if not body.job_ids:
        raise HTTPException(422, "job_ids must not be empty")
    now = datetime.now(timezone.utc)
    # Resolve every id BEFORE touching a single row: a 404 half-way through a
    # selection must leave nothing marked, so the user can fix the request and
    # retry without wondering which jobs already got flagged.
    jobs: list[JobPosting] = []
    for job_id in body.job_ids:
        job = session.get(JobPosting, job_id)
        if job is None:
            raise HTTPException(404, f"job {job_id} not found")
        jobs.append(job)
    results = [_mark_unemployment(j, used=body.used, now=now) for j in jobs]
    for app_row in results:
        session.add(app_row)
    session.commit()
    return [_application_out(a) for a in results]


@app.get("/api/pending-match", response_model=list[PendingMatchJob])
def pending_match(
    limit: int = 25,
    include_stale: bool = False,
    session: Session = Depends(get_session),
) -> list[PendingMatchJob]:
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
    """Count of baseline scores not against the active resume, on the active
    profile's postings (the set "Re-score" and "Keep existing scores" act on).

    Returns 0 when there's no active resume — there's nothing to be stale
    against in that case.
    """
    active_id = _active_resume_id(session)
    if active_id is None:
        return {"count": 0}
    count = len(session.exec(services.stale_scores_stmt(session, active_id)).all())
    return {"count": count}


@app.post("/api/scores/adopt")
def adopt_stale_scores(session: Session = Depends(get_session)) -> dict:
    """Keep existing baseline scores after a *minor* resume edit.

    Re-stamps stale scores onto the active resume rather than re-running them, so
    a typo fix doesn't cost an AI call per job. The "is this edit small enough?"
    judgment is the user's — the resume page asks right after an upload.
    """
    resume = services.active_resume(session)
    if resume is None:
        raise HTTPException(409, "no active resume to adopt scores onto")
    return {"count": services.adopt_scores(session, resume_id=resume.id)}


@app.post("/api/jobs/{job_id}/score", response_model=ScoreOut)
def upsert_score(job_id: int, body: ScoreIn, session: Session = Depends(get_session)) -> ScoreOut:
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
) -> list[ScoreOut]:
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
def list_companies(session: Session = Depends(get_session)) -> list[CompanyOut]:
    companies = session.exec(select(Company).order_by(Company.name)).all()
    return [_company_out(c) for c in companies]


@app.post("/api/companies/{company_id}/block", response_model=CompanyOut)
def block_company(company_id: int, blocked: bool = True, session: Session = Depends(get_session)) -> CompanyOut:
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
