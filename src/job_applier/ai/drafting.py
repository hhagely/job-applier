"""Tailored draft orchestration: generate resume + cover-letter markdown, enforce
the ATS character bans, render both PDFs via the Phase 2 print path, and chain the
tailored re-score. Mirrors the `/draft` -> `/score-draft` slash-command flow.
"""

from __future__ import annotations

import logging
import os
import tempfile
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Callable, Optional

from pydantic import BaseModel, ValidationError, model_validator

from job_applier import services
from job_applier.ai import bans, providers, scoring
from job_applier.config import settings
from job_applier.models.db import ApplicationStatus, JobPosting, Session

log = logging.getLogger(__name__)

# Statuses the user has advanced past drafting — auto-drafting must not regress them.
_ADVANCED_STATUSES = frozenset(
    {
        ApplicationStatus.applied,
        ApplicationStatus.screening,
        ApplicationStatus.interviewing,
        ApplicationStatus.rejected,
    }
)


class DraftingError(Exception):
    """The provider output couldn't be parsed into the two markdown documents."""


class DraftEnvelope(BaseModel):
    resume_md: str
    cover_letter_md: str

    @model_validator(mode="after")
    def _non_empty(self) -> "DraftEnvelope":
        if not self.resume_md.strip() or not self.cover_letter_md.strip():
            raise ValueError("resume_md and cover_letter_md must both be non-empty")
        return self


@dataclass
class DraftResult:
    job_id: int
    tailored_score: Optional[int]
    sanitized: bool  # whether any banned chars were replaced
    stages: list[str] = field(default_factory=list)


_template_cache: Optional[str] = None


def _template() -> str:
    global _template_cache
    if _template_cache is None:
        _template_cache = (
            resources.files("job_applier.ai")
            .joinpath("prompts/draft.md")
            .read_text(encoding="utf-8")
        )
    return _template_cache


def build_draft_prompt(resume_text: str, job: JobPosting) -> str:
    return (
        _template()
        .replace("{{RESUME_TEXT}}", resume_text.strip())
        .replace("{{TITLE}}", job.title or "")
        .replace("{{COMPANY}}", job.company.name if job.company else "Unknown")
        .replace("{{LOCATION}}", job.location or "Not specified")
        .replace("{{DESCRIPTION}}", scoring.html_to_text(job.description or ""))
    )


def _run_and_parse(provider: str, prompt: str, model: Optional[str]) -> DraftEnvelope:
    last_err: Optional[Exception] = None
    for attempt in range(2):
        text = prompt
        if attempt == 1:
            text += "\n\nIMPORTANT: return ONLY the JSON object with resume_md and cover_letter_md."
        raw = providers.run(
            provider, text, expect_json=True, model=model, timeout=settings.ai_draft_timeout
        )
        try:
            data = providers.extract_json(raw)
            return DraftEnvelope.model_validate(data)
        except (ValueError, ValidationError) as exc:
            last_err = exc
    raise DraftingError(f"invalid draft JSON after retry: {last_err}")


def _render_html_to_pdf(html: str) -> bytes:
    """Print standalone HTML to PDF via the swappable driver, using a file URL so
    no running server is needed (same CSS/seam as the Phase 2 endpoint)."""
    from job_applier import pdf

    with tempfile.NamedTemporaryFile(
        "w", suffix=".html", delete=False, encoding="utf-8"
    ) as f:
        f.write(html)
        path = f.name
    try:
        return pdf.render_to_pdf(Path(path).as_uri())
    finally:
        os.unlink(path)


def generate_draft(
    session: Session,
    provider: str,
    job: JobPosting,
    *,
    model: Optional[str] = None,
    progress_cb: Optional[Callable[[str], None]] = None,
) -> DraftResult:
    """Generate + persist a tailored draft for one job, then re-score it.

    Stages: drafting -> rendering -> scoring -> done. Character bans are enforced
    server-side (sanitize + assert) so output is submission-safe regardless of
    provider.
    """
    from job_applier import drafts

    def _stage(name: str) -> None:
        if progress_cb is not None:
            progress_cb(name)

    resume = services.active_resume(session)
    if resume is None:
        raise scoring.NoActiveResume("no active resume — upload one first")

    _stage("drafting")
    envelope = _run_and_parse(
        provider, build_draft_prompt(resume.extracted_text, job), model
    )

    resume_md = bans.sanitize(envelope.resume_md)
    cover_md = bans.sanitize(envelope.cover_letter_md)
    sanitized = resume_md != envelope.resume_md or cover_md != envelope.cover_letter_md
    leftover = bans.find_banned(resume_md) + bans.find_banned(cover_md)
    if leftover:
        # Every banned char has a replacement, so this should never happen; if it
        # does, refuse to persist banned output rather than ship it.
        raise DraftingError(f"banned characters could not be sanitized: {leftover}")

    drafts.save_markdown(job.id, resume_md, cover_md)

    _stage("rendering")
    for kind, md in (("resume", resume_md), ("cover_letter", cover_md)):
        html = drafts.render_print_html(md, kind)  # type: ignore[arg-type]
        drafts.render_pdf(job.id, kind, _render_html_to_pdf(html))  # type: ignore[arg-type]

    # Drafting a job marks it "drafted" so the queue's status filters stay honest,
    # but never regress a job the user has already advanced past drafting.
    current_status = job.application.status if job.application else None
    if current_status not in _ADVANCED_STATUSES:
        services.bulk_set_status(session, [job.id], ApplicationStatus.drafted)

    _stage("scoring")
    tailored_score: Optional[int] = None
    try:
        result = scoring.score_one(
            session, provider, resume_md, job, model=model, score_kind="tailored"
        )
        tailored_score = result.score
    except Exception as exc:  # noqa: BLE001 - a scoring hiccup must not lose the saved draft
        log.warning("tailored re-score failed for job %s: %s", job.id, exc)
        tailored_score = None

    _stage("done")
    return DraftResult(
        job_id=job.id,
        tailored_score=tailored_score,
        sanitized=sanitized,
        stages=["drafting", "rendering", "scoring", "done"],
    )
