"""Shared FastAPI dependencies for the API routers.

Keeping the resolve-or-404 job lookup and the AI-readiness guard here means the
same 404/409 behavior is declared once and injected, rather than copied into
every endpoint that needs it.
"""

from __future__ import annotations

from fastapi import Depends, HTTPException
from sqlmodel import Session

from job_applier import services
from job_applier.models.db import JobPosting, get_session, get_setting


def require_job(job_id: int, session: Session = Depends(get_session)) -> JobPosting:
    """Resolve the path ``job_id`` to a row or raise 404. Inject as
    ``job: JobPosting = Depends(require_job)``; endpoints that only need the
    existence check can ignore the returned row."""
    job = session.get(JobPosting, job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    return job


def require_ai_ready(session: Session = Depends(get_session)) -> str:
    """Ensure an AI provider is selected and an active resume exists, returning the
    provider name. Raises 409 (provider first, then resume) otherwise."""
    provider = get_setting(session, "ai_provider")
    if not provider:
        raise HTTPException(409, "no AI provider selected — pick one in Settings")
    if services.active_resume(session) is None:
        raise HTTPException(409, "no active resume — upload one on the Resume page")
    return provider
