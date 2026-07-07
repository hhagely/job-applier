"""Scoring orchestration: render the shared rubric prompt, run it through the
selected provider, validate/parse the JSON, and persist via the shared score
service. Same rubric text as the ``/match-pending`` slash command (single source
of truth in ``prompts/score.md``).
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from importlib import resources
from typing import Callable, Optional

from pydantic import BaseModel, Field, ValidationError

from job_applier.ai import providers
from job_applier.api import services
from job_applier.api.schemas import ScoreIn
from job_applier.models.db import ApplicationStatus, JobPosting, Session, engine

# Below this score a job is auto-archived after scoring (60 itself survives).
ARCHIVE_BELOW = 60

_TAG_RE = re.compile(r"<[^>]+>")


class NoActiveResume(Exception):
    """No active resume to score against."""


class ScoringError(Exception):
    """The provider output couldn't be parsed/validated into a score."""


def html_to_text(s: str) -> str:
    """Flatten a scraped HTML job description to readable plain text."""
    if not s:
        return ""
    s = re.sub(r"<\s*/?(p|div|li|h[1-6])\s*>", "\n", s, flags=re.IGNORECASE)
    s = re.sub(r"<\s*br\s*/?\s*>", "\n", s, flags=re.IGNORECASE)
    s = _TAG_RE.sub("", s)
    s = html.unescape(s)
    # Collapse runs of blank lines the tag substitution can create.
    return re.sub(r"\n{3,}", "\n\n", s).strip()


# ---- prompt template ------------------------------------------------------

_template_cache: Optional[str] = None


def _template() -> str:
    global _template_cache
    if _template_cache is None:
        _template_cache = (
            resources.files("job_applier.ai")
            .joinpath("prompts/score.md")
            .read_text(encoding="utf-8")
        )
    return _template_cache


def build_score_prompt(resume_text: str, job: JobPosting) -> str:
    """Render the score prompt for one job. Description HTML is flattened first."""
    return (
        _template()
        .replace("{{RESUME_TEXT}}", resume_text.strip())
        .replace("{{TITLE}}", job.title or "")
        .replace("{{COMPANY}}", job.company.name if job.company else "Unknown")
        .replace("{{LOCATION}}", job.location or "Not specified")
        .replace("{{DESCRIPTION}}", html_to_text(job.description or ""))
    )


# ---- payload validation ---------------------------------------------------


class ScoredPayload(BaseModel):
    score: int = Field(ge=0, le=100)
    rubric: dict = {}
    reasoning: str = ""


_BUCKETS = (
    "skills_overlap",
    "experience_match",
    "role_fit",
    "domain_fit",
    "hard_requirements",
)


def _reconcile_score(payload: ScoredPayload) -> int:
    """If the rubric buckets are all present with numeric points, trust their sum
    over a mismatched top-level score (models sometimes fumble the addition)."""
    try:
        points = [int(payload.rubric[b]["points"]) for b in _BUCKETS]
    except (KeyError, TypeError, ValueError):
        return payload.score
    bucket_sum = sum(points)
    if abs(bucket_sum - payload.score) > 5:
        return max(0, min(100, bucket_sum))
    return payload.score


# ---- scoring --------------------------------------------------------------


@dataclass
class ScoreResult:
    job_id: int
    score: int
    reasoning: str


@dataclass
class JobScoreOutcome:
    job_id: int
    title: str
    score: Optional[int]
    error: Optional[str]


def _run_and_parse(provider: str, prompt: str, model: Optional[str]) -> ScoredPayload:
    """Run the provider and parse strict JSON, retrying once with a nudge."""
    last_err: Optional[Exception] = None
    for attempt in range(2):
        text = prompt
        if attempt == 1:
            text += "\n\nIMPORTANT: return ONLY the JSON object, no prose or fences."
        raw = providers.run(provider, text, expect_json=True, model=model)
        try:
            data = providers.extract_json(raw)
            return ScoredPayload.model_validate(data)
        except (ValueError, ValidationError) as exc:
            last_err = exc
    raise ScoringError(f"invalid JSON after retry: {last_err}")


def score_one(
    session: Session,
    provider: str,
    resume_text: str,
    job: JobPosting,
    *,
    model: Optional[str] = None,
) -> ScoreResult:
    """Score a single job and persist it through the shared upsert service."""
    payload = _run_and_parse(provider, build_score_prompt(resume_text, job), model)
    final_score = _reconcile_score(payload)
    services.upsert_score(
        session,
        job.id,
        ScoreIn(
            score=final_score,
            rubric=payload.rubric,
            reasoning=payload.reasoning,
            scored_by=f"{provider}-cli",
            score_kind="baseline",
        ),
    )
    return ScoreResult(job.id, final_score, payload.reasoning)


def score_pending(
    session: Session,
    *,
    provider: str,
    model: Optional[str] = None,
    job_ids: Optional[list[int]] = None,
    include_stale: bool = True,
    limit: int = 200,
    progress_cb: Optional[Callable[[JobScoreOutcome], None]] = None,
) -> list[JobScoreOutcome]:
    """Score a batch of jobs, then auto-archive any that scored `< 60`.

    A single job's failure is recorded as a per-job error and does not abort the
    batch. When ``job_ids`` is given those exact jobs are scored; otherwise the
    live pending-match queue is used.
    """
    resume = services.active_resume(session)
    if resume is None:
        raise NoActiveResume("no active resume — upload one first")

    if job_ids is not None:
        jobs = [j for j in (session.get(JobPosting, jid) for jid in job_ids) if j]
    else:
        jobs = services.select_pending_jobs(
            session, limit=limit, include_stale=include_stale
        )

    outcomes: list[JobScoreOutcome] = []
    low_scorers: list[int] = []
    for job in jobs:
        try:
            result = score_one(session, provider, resume.extracted_text, job, model=model)
            outcome = JobScoreOutcome(job.id, job.title, result.score, None)
            if result.score < ARCHIVE_BELOW:
                low_scorers.append(job.id)
        except Exception as exc:  # noqa: BLE001 - one job's failure can't kill the batch
            outcome = JobScoreOutcome(job.id, job.title, None, str(exc))
        outcomes.append(outcome)
        if progress_cb is not None:
            progress_cb(outcome)

    if low_scorers:
        services.bulk_set_status(session, low_scorers, ApplicationStatus.archived)
    return outcomes


def open_session() -> Session:
    """A fresh Session on the app engine. Isolated here so the background task can
    open its own handle (no cross-thread SQLite sharing) and tests can override it."""
    return Session(engine())
