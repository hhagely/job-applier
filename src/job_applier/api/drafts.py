"""Tailored-draft endpoints: read draft status, save/render the resume +
cover-letter markdown to PDF, serve the print-ready HTML the PDF driver loads,
download the rendered PDFs, and kick off a background AI draft run.
"""

from __future__ import annotations

import functools

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from sqlmodel import Session

from job_applier import drafts, pdf
from job_applier.ai import tasks as ai_tasks
from job_applier.api import ai as ai_endpoints
from job_applier.api import services
from job_applier.api.schemas import DraftIn, DraftOut, StartTaskOut
from job_applier.models.db import JobPosting, get_session, get_setting
from job_applier.pdf import PdfRendererUnavailable

router = APIRouter(tags=["drafts"])


def _draft_out(job_id: int, *, include_markdown: bool = False) -> DraftOut:
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


@router.get("/api/jobs/{job_id}/draft", response_model=DraftOut)
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


@router.post("/api/jobs/{job_id}/draft", response_model=DraftOut)
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


@router.post("/api/jobs/{job_id}/draft/render", response_model=DraftOut)
def render_draft(job_id: int, request: Request, session: Session = Depends(get_session)):
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


@router.get("/api/jobs/{job_id}/draft/{kind}/print.html", response_class=HTMLResponse)
def draft_print_html(job_id: int, kind: str, session: Session = Depends(get_session)):
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


@router.get("/api/jobs/{job_id}/draft/resume.pdf")
def download_draft_resume(job_id: int, session: Session = Depends(get_session)):
    if session.get(JobPosting, job_id) is None:
        raise HTTPException(404, "job not found")
    path = drafts.pdf_path(job_id, "resume")
    if not path.exists():
        raise HTTPException(404, "tailored resume PDF not found — run /draft first")
    return FileResponse(path, media_type="application/pdf", filename=f"resume-{job_id}.pdf")


@router.get("/api/jobs/{job_id}/draft/cover-letter.pdf")
def download_draft_cover_letter(job_id: int, session: Session = Depends(get_session)):
    if session.get(JobPosting, job_id) is None:
        raise HTTPException(404, "job not found")
    path = drafts.pdf_path(job_id, "cover_letter")
    if not path.exists():
        raise HTTPException(404, "cover letter PDF not found — run /draft first")
    return FileResponse(
        path, media_type="application/pdf", filename=f"cover-letter-{job_id}.pdf"
    )


@router.post("/api/jobs/{job_id}/ai/draft", response_model=StartTaskOut)
def start_ai_draft(job_id: int, session: Session = Depends(get_session)):
    """Start a background tailored-draft run (draft -> render PDFs -> re-score).
    Poll GET /api/ai/tasks/{id} for staged progress."""
    if session.get(JobPosting, job_id) is None:
        raise HTTPException(404, "job not found")
    provider = get_setting(session, "ai_provider")
    if not provider:
        raise HTTPException(409, "no AI provider selected — pick one in Settings")
    if services.active_resume(session) is None:
        raise HTTPException(409, "no active resume — upload one on the Resume page")
    model = get_setting(session, "ai_model")
    fn = functools.partial(
        ai_endpoints.run_generate_draft_task,
        provider=provider,
        model=model,
        job_id=job_id,
    )
    task_id = ai_tasks.start_task("draft", ai_endpoints.DRAFT_TASK_STEPS, fn)
    return StartTaskOut(task_id=task_id)
