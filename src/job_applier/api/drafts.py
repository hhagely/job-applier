"""Tailored-draft endpoints: read draft status, save/render the resume +
cover-letter markdown to PDF, serve the print-ready HTML the PDF driver loads,
download the rendered PDFs, and kick off a background AI draft run.
"""

from __future__ import annotations

import functools
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from sqlmodel import Session

from job_applier import drafts, pdf, profiles
from job_applier.ai import tasks as ai_tasks
from job_applier.api import ai as ai_endpoints
from job_applier.api.deps import require_ai_ready, require_job
from job_applier.api.schemas import DraftIn, DraftOut, StartTaskOut
from job_applier.models.db import JobPosting, get_session
from job_applier.models.scoping import session_profile_id
from job_applier.pdf import PdfRendererUnavailable

router = APIRouter(tags=["drafts"])


def _draft_out(job_id: int, profile_id: int, *, include_markdown: bool = False) -> DraftOut:
    s = drafts.get_status(job_id, profile_id=profile_id)
    return DraftOut(
        job_id=s.job_id,
        has_resume_md=s.has_resume_md,
        has_resume_pdf=s.has_resume_pdf,
        has_cover_letter_md=s.has_cover_letter_md,
        has_cover_letter_pdf=s.has_cover_letter_pdf,
        updated_at=s.updated_at,
        resume_md=(
            drafts.read_markdown(job_id, "resume", profile_id=profile_id)
            if include_markdown
            else None
        ),
        cover_letter_md=(
            drafts.read_markdown(job_id, "cover_letter", profile_id=profile_id)
            if include_markdown
            else None
        ),
    )


@router.get("/api/jobs/{job_id}/draft", response_model=DraftOut)
def get_draft(
    include_markdown: bool = False,
    job: JobPosting = Depends(require_job),
    session: Session = Depends(get_session),
) -> DraftOut:
    return _draft_out(job.id, profiles.ensure_profile_id(session), include_markdown=include_markdown)


def _print_url(request: Request, job_id: int, kind: drafts.DraftKind, profile_id: int) -> str:
    """Absolute loopback URL of the print-HTML endpoint the PDF driver loads.

    Carries the profile explicitly: the driver's request is a separate HTTP call,
    and it must print the same profile's markdown even if the user switches
    profiles while it runs.
    """
    base = str(request.base_url).rstrip("/")
    return f"{base}/api/jobs/{job_id}/draft/{kind}/print.html?profile_id={profile_id}"


def _render_draft_pdfs(request: Request, job_id: int, profile_id: int) -> None:
    """Print a PDF for every draft kind that has markdown on disk.

    The browser engine (headless Chromium here, Electron in the packaged app)
    loads the print-HTML endpoint over loopback and prints it; we persist the
    bytes next to the markdown. Markdown is already saved by the caller, so a
    renderer failure never loses the draft text.

    Raises ``HTTPException(503)`` for both ways this step can fail: no usable
    browser engine (``PdfRendererUnavailable``) and a failed write of the printed
    bytes (``OSError`` — full disk, read-only volume, permissions). The second used
    to escape as an opaque 500 even though the outcome is identical from the
    caller's side: the draft markdown is safe, only the PDF is missing.
    """
    for kind in drafts.existing_markdown_kinds(job_id, profile_id=profile_id):
        try:
            pdf_bytes = pdf.render_to_pdf(_print_url(request, job_id, kind, profile_id))
            drafts.render_pdf(job_id, kind, pdf_bytes, profile_id=profile_id)
        except PdfRendererUnavailable as exc:
            raise HTTPException(503, str(exc)) from exc
        except OSError as exc:
            raise HTTPException(
                503, f"couldn't write the {kind} PDF: {exc}"
            ) from exc


@router.post("/api/jobs/{job_id}/draft", response_model=DraftOut)
def save_draft(
    body: DraftIn,
    request: Request,
    job: JobPosting = Depends(require_job),
    session: Session = Depends(get_session),
) -> DraftOut:
    if body.resume_md is None and body.cover_letter_md is None:
        raise HTTPException(422, "provide at least one of resume_md, cover_letter_md")
    profile_id = profiles.ensure_profile_id(session)
    try:
        drafts.save_markdown(
            job.id, body.resume_md, body.cover_letter_md, profile_id=profile_id
        )
    except OSError as exc:
        # The markdown write itself failed (full disk, read-only volume), so unlike
        # the render step below NOTHING was saved. Say so: the user's edits are only
        # in their browser, and a message promising a persisted draft would lose them.
        raise HTTPException(
            503, f"couldn't save the draft markdown, nothing was written: {exc}"
        ) from exc
    # Raises 503 on either failure mode; the markdown above is already persisted.
    _render_draft_pdfs(request, job.id, profile_id)
    return _draft_out(job.id, profile_id)


@router.post("/api/jobs/{job_id}/draft/render", response_model=DraftOut)
def render_draft(
    request: Request,
    job: JobPosting = Depends(require_job),
    session: Session = Depends(get_session),
) -> DraftOut:
    profile_id = profiles.ensure_profile_id(session)
    s = drafts.get_status(job.id, profile_id=profile_id)
    if not (s.has_resume_md or s.has_cover_letter_md):
        raise HTTPException(404, "no draft markdown to render")
    _render_draft_pdfs(request, job.id, profile_id)
    return _draft_out(job.id, profile_id)


@router.get("/api/jobs/{job_id}/draft/{kind}/print.html", response_class=HTMLResponse)
def draft_print_html(
    job_id: int,
    kind: str,
    profile_id: Optional[int] = None,
    session: Session = Depends(get_session),
) -> HTMLResponse:
    """Standalone print-ready HTML for a draft kind. The PDF driver / Electron
    loads this and prints it to PDF, so the CSS lives in one place. The PDF
    driver passes ``profile_id``; a direct visit falls back to the active one."""
    if kind not in ("resume", "cover_letter"):
        raise HTTPException(404, "unknown draft kind")
    if session.get(JobPosting, job_id) is None:
        raise HTTPException(404, "job not found")
    pid = profile_id if profile_id is not None else profiles.ensure_profile_id(session)
    md = drafts.read_markdown(job_id, kind, profile_id=pid)  # type: ignore[arg-type]
    if md is None:
        raise HTTPException(404, "draft markdown not found")
    return HTMLResponse(drafts.render_print_html(md, kind))  # type: ignore[arg-type]


@router.get("/api/jobs/{job_id}/draft/resume.pdf")
def download_draft_resume(
    job: JobPosting = Depends(require_job), session: Session = Depends(get_session)
) -> FileResponse:
    path = drafts.pdf_path(job.id, "resume", profile_id=profiles.ensure_profile_id(session))
    if not path.exists():
        raise HTTPException(404, "tailored resume PDF not found — run /draft first")
    return FileResponse(path, media_type="application/pdf", filename=f"resume-{job.id}.pdf")


@router.get("/api/jobs/{job_id}/draft/cover-letter.pdf")
def download_draft_cover_letter(
    job: JobPosting = Depends(require_job), session: Session = Depends(get_session)
) -> FileResponse:
    path = drafts.pdf_path(job.id, "cover_letter", profile_id=profiles.ensure_profile_id(session))
    if not path.exists():
        raise HTTPException(404, "cover letter PDF not found — run /draft first")
    return FileResponse(
        path, media_type="application/pdf", filename=f"cover-letter-{job.id}.pdf"
    )


@router.post("/api/jobs/{job_id}/ai/draft", response_model=StartTaskOut)
def start_ai_draft(
    job: JobPosting = Depends(require_job),
    provider: str = Depends(require_ai_ready),
    session: Session = Depends(get_session),
) -> StartTaskOut:
    """Start a background tailored-draft run (draft -> render PDFs -> re-score).
    Poll GET /api/ai/tasks/{id} for staged progress."""
    model = ai_endpoints.generation_model(session, provider)
    fn = functools.partial(
        ai_endpoints.run_generate_draft_task,
        provider=provider,
        model=model,
        job_id=job.id,
    )
    task_id = ai_tasks.start_task(
        "draft",
        ai_endpoints.DRAFT_TASK_STEPS,
        fn,
        ref=str(job.id),
        profile_id=session_profile_id(session),
    )
    return StartTaskOut(task_id=task_id)
