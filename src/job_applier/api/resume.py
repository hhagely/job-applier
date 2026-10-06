"""Resume upload + retrieval endpoints.

The active resume is the one scored against and tailored from; uploading a new
one demotes the previous active (history is preserved) and becomes the active
search profile's resume.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse
from sqlmodel import Session, select

from job_applier import profiles, resume_io
from job_applier.api.schemas import ResumeOut, ResumeSummaryOut
from job_applier.config import settings
from job_applier.models.db import Resume, get_session

router = APIRouter(tags=["resume"])


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
    r = profiles.active_resume(session)
    if r is None:
        raise HTTPException(404, "no active resume — POST /api/resume to upload")
    return r


@router.post("/api/resume", response_model=ResumeOut, status_code=201)
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

    resume = Resume(
        original_filename=file.filename,
        pdf_path=str(pdf_path),
        extracted_text=text,
        page_count=page_count,
    )
    session.add(resume)
    session.flush()
    # Demotes the previously-active resume (history is preserved).
    profiles.set_active_resume(session, resume.id)
    profiles.adopt_uploaded_resume(session, resume.id)
    session.commit()
    session.refresh(resume)
    return _resume_out(resume)


@router.get("/api/resumes", response_model=list[ResumeSummaryOut])
def list_resumes(session: Session = Depends(get_session)):
    """Every uploaded resume, newest first, for the per-profile resume picker.

    Leaves out the extracted text so the list stays light.
    """
    rows = session.exec(select(Resume).order_by(Resume.uploaded_at.desc())).all()
    return [
        ResumeSummaryOut(
            id=r.id,
            original_filename=r.original_filename,
            is_active=r.is_active,
            uploaded_at=r.uploaded_at,
        )
        for r in rows
    ]


@router.post("/api/resumes/{resume_id}/use", response_model=ResumeOut)
def use_resume(resume_id: int, session: Session = Depends(get_session)):
    """Make another of the active profile's uploads the one it scores and
    tailors with. Scores made against the previous one then read as stale."""
    profile = profiles.load_or_create_profile(session)
    try:
        profiles.update_profile_meta(session, profile.id, resume_id=resume_id)
    except LookupError as e:
        raise HTTPException(404, str(e)) from e
    return _resume_out(_active_resume(session))


@router.get("/api/resume/current", response_model=ResumeOut)
def get_current_resume(session: Session = Depends(get_session)):
    return _resume_out(_active_resume(session))


@router.get("/api/resume/current/pdf")
def download_current_resume(session: Session = Depends(get_session)):
    r = _active_resume(session)
    return FileResponse(
        r.pdf_path,
        media_type="application/pdf",
        filename=r.original_filename,
    )


@router.get("/api/resume/current/markdown", response_class=PlainTextResponse)
def get_current_resume_markdown(session: Session = Depends(get_session)):
    r = _active_resume(session)
    return resume_io.to_markdown(r.extracted_text)
