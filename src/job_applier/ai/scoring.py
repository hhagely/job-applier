"""Scoring orchestration: render the shared rubric prompt, run it through the
selected provider, validate/parse the JSON, and persist via the shared score
service. Same rubric text as the ``/match-pending`` slash command (single source
of truth in ``prompts/score.md``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from pydantic import BaseModel, Field

from job_applier import services
from job_applier.ai import prompt_safety, providers
from job_applier.ai.templates import load_template, render_job_prompt
from job_applier.config import settings
from job_applier.contracts import html_to_text
from job_applier.filters import load_active_config
from job_applier.models.db import ApplicationStatus, JobPosting, Session, engine

# Below this score a job is auto-archived after scoring (60 itself survives).
ARCHIVE_BELOW = 60

# Batch scoring: pack several jobs into one CLI call so the resume + rubric prefix
# (the bulk of each call, and identical across jobs) is sent once instead of once
# per job. Sizing is adaptive — short JDs pack many per call, long JDs pack fewer,
# so full descriptions are never truncated and one call's output can't balloon.
BATCH_MAX_JOBS = 8
# Total JD characters allowed in one batch. A single JD larger than this lands in a
# batch of one (i.e. the single-job path), losing no content.
BATCH_JD_CHAR_BUDGET = 24_000

class NoActiveResume(Exception):
    """No active resume to score against."""


class ScoringError(Exception):
    """The provider output couldn't be parsed/validated into a score."""


def _state_rule_clause(home_state: Optional[str]) -> str:
    """The home-state allow-list hard rule for the score prompts, filled into the
    ``{{STATE_RULE}}`` placeholder. Empty when no home state is configured, so the
    rule simply doesn't appear (matching the ingest filter, which skips it too)."""
    if not home_state:
        return ""
    return (
        f"- The posting names a US-state allow-list that omits {home_state}: "
        f'score `0`, reasoning\n  `"state allow-list excludes {home_state}"`.'
    )


def build_score_prompt(
    resume_text: str, job: JobPosting, *, home_state: Optional[str] = None
) -> str:
    """Render the score prompt for one job (shared single-job render)."""
    return render_job_prompt(
        "score.md", resume_text, job, state_rule=_state_rule_clause(home_state)
    )


def _job_block(job: JobPosting, nonce: str) -> str:
    """One delimited, id-tagged job block for the batch prompt. The full JD is fenced
    (with the shared per-call nonce and its markers scrubbed from the JD text) so a
    long/messy or hostile description can't bleed into the next job or be read as
    instructions."""
    company = job.company.name if job.company else "Unknown"
    description = prompt_safety.clean_untrusted(html_to_text(job.description or ""), nonce)
    return (
        f"=== JOB id={job.id} nonce={nonce} ===\n"
        f"Title: {job.title or ''}\n"
        f"Company: {company}\n"
        f"Location: {job.location or 'Not specified'}\n"
        "Description:\n"
        "<<<\n"
        f"{description}\n"
        ">>>\n"
        f"=== END JOB id={job.id} nonce={nonce} ==="
    )


def build_batch_score_prompt(
    resume_text: str, jobs: list[JobPosting], *, home_state: Optional[str] = None
) -> str:
    """Render the batch score prompt for several jobs (resume + rubric sent once).

    All job blocks share one per-call nonce; the guard text tells the model the block
    ends only at the END line carrying it, so no job's description can forge a close."""
    nonce = prompt_safety.new_nonce()
    jobs_block = "\n\n".join(_job_block(j, nonce) for j in jobs)
    return (
        load_template("score_batch.md")
        .replace("{{RESUME_TEXT}}", resume_text.strip())
        .replace("{{NONCE}}", nonce)
        .replace("{{STATE_RULE}}", _state_rule_clause(home_state))
        .replace("{{JOBS_BLOCK}}", jobs_block)
    )


def chunk_jobs(jobs: list[JobPosting]) -> list[list[JobPosting]]:
    """Greedily pack jobs into batches bounded by BATCH_MAX_JOBS and a JD-character
    budget. Short descriptions pack up to the count cap; long ones split earlier so
    each call stays bounded. A single over-budget JD gets its own batch of one."""
    batches: list[list[JobPosting]] = []
    current: list[JobPosting] = []
    current_chars = 0
    for job in jobs:
        jd_chars = len(job.description or "")
        would_overflow = current and (
            len(current) >= BATCH_MAX_JOBS
            or current_chars + jd_chars > BATCH_JD_CHAR_BUDGET
        )
        if would_overflow:
            batches.append(current)
            current, current_chars = [], 0
        current.append(job)
        current_chars += jd_chars
    if current:
        batches.append(current)
    return batches


# ---- payload validation ---------------------------------------------------


class ScoredPayload(BaseModel):
    score: int = Field(ge=0, le=100)
    rubric: dict = {}
    reasoning: str = ""


class BatchJobScore(BaseModel):
    """One job's result inside a batch. ``score`` is optional/loosely typed so a
    single malformed entry doesn't fail validation of the whole batch — the caller
    accepts only entries with an in-range score and re-queues the rest single-job."""

    id: int
    score: Optional[int] = None
    rubric: dict = {}
    reasoning: str = ""
    error: Optional[str] = None


class BatchScoredPayload(BaseModel):
    results: list[BatchJobScore] = []


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


def _is_untriaged(job: JobPosting) -> bool:
    """True when the user hasn't explicitly triaged this job, so auto-archiving a
    low score won't clobber a manually-set status (``applied``, ``interviewing``,
    ...). A missing application row or the default ``new`` status both count as
    untriaged; anything else means the user has acted on the job and we leave it be.
    """
    app = job.application
    return app is None or app.status == ApplicationStatus.new


def _run_and_parse(provider: str, prompt: str, model: Optional[str]) -> ScoredPayload:
    """Run the provider and parse strict JSON into a ScoredPayload."""
    return providers.run_json_or(
        provider, prompt, ScoredPayload, error_cls=ScoringError, label="score", model=model
    )


def _persist_score(
    session: Session,
    provider: str,
    job_id: int,
    payload: ScoredPayload,
    *,
    score_kind: str = "baseline",
) -> int:
    """Reconcile + upsert one scored payload. Shared by the single-job and batch paths."""
    final_score = _reconcile_score(payload)
    services.upsert_score(
        session,
        job_id,
        score=final_score,
        rubric=payload.rubric,
        reasoning=payload.reasoning,
        scored_by=f"{provider}-cli",
        score_kind=score_kind,
    )
    return final_score


def score_one(
    session: Session,
    provider: str,
    resume_text: str,
    job: JobPosting,
    *,
    model: Optional[str] = None,
    score_kind: str = "baseline",
) -> ScoreResult:
    """Score ``resume_text`` against a job and persist via the shared upsert service.

    ``score_kind="tailored"`` scores a per-job tailored resume markdown (the drafting
    flow); the same rubric template powers both so baseline/tailored can't drift.
    """
    home_state = load_active_config(session).home_state
    prompt = build_score_prompt(resume_text, job, home_state=home_state)
    payload = _run_and_parse(provider, prompt, model)
    final_score = _persist_score(session, provider, job.id, payload, score_kind=score_kind)
    return ScoreResult(job.id, final_score, payload.reasoning)


def _score_batch_call(
    provider: str,
    resume_text: str,
    jobs: list[JobPosting],
    model: Optional[str],
    home_state: Optional[str] = None,
) -> dict[int, ScoredPayload]:
    """One batch invocation. Returns ``{job_id: payload}`` for the jobs the model
    scored cleanly (id echoed, no ``error``, score in range). Jobs the model dropped
    or botched are simply absent, so the caller re-queues them single-job. Raises the
    underlying ``ProviderError`` / ``ScoringError`` on a total failure (provider error
    or unparseable JSON after retry) so the caller can fall the *whole* batch back."""
    prompt = build_batch_score_prompt(resume_text, jobs, home_state=home_state)
    payload = providers.run_json_or(
        provider,
        prompt,
        BatchScoredPayload,
        error_cls=ScoringError,
        label="batch",
        model=model,
        timeout=settings.ai_score_batch_timeout,
        nudge=(
            "IMPORTANT: return ONLY the JSON object with a `results` array, one "
            "entry per job id, each with its `id` and `score`."
        ),
    )

    wanted = {j.id for j in jobs}
    out: dict[int, ScoredPayload] = {}
    for r in payload.results:
        if r.id not in wanted or r.error is not None or r.score is None:
            continue
        if not 0 <= r.score <= 100:
            continue
        out[r.id] = ScoredPayload(score=r.score, rubric=r.rubric, reasoning=r.reasoning)
    return out


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
    """Score a batch of jobs, then auto-archive any *untriaged* job that scored `< 60`.

    Only jobs the user hasn't acted on (no application row, or still ``new``) are
    auto-archived — a job manually marked ``applied``/``interviewing``/etc. keeps its
    status even if a re-score drops it below the threshold (see ``_is_untriaged``).

    Jobs are scored in adaptive batches — one CLI call per batch, resume + rubric
    sent once — with a per-job single-job fallback: any job the model drops or botches
    (and any whole batch whose call fails) is re-scored one at a time so batching never
    loses a job. A single job's failure is recorded as a per-job error and does not abort
    the run. When ``job_ids`` is given those exact jobs are scored; otherwise the live
    pending-match queue is used.
    """
    resume = services.active_resume(session)
    if resume is None:
        raise NoActiveResume("no active resume — upload one first")

    home_state = load_active_config(session).home_state

    if job_ids is not None:
        jobs = [j for j in (session.get(JobPosting, jid) for jid in job_ids) if j]
    else:
        jobs = services.select_pending_jobs(
            session, limit=limit, include_stale=include_stale
        )

    outcomes: list[JobScoreOutcome] = []
    low_scorers: list[int] = []

    def _record(job: JobPosting, score: Optional[int], error: Optional[str]) -> None:
        if score is not None and score < ARCHIVE_BELOW and _is_untriaged(job):
            low_scorers.append(job.id)
        outcome = JobScoreOutcome(job.id, job.title, score, error)
        outcomes.append(outcome)
        if progress_cb is not None:
            progress_cb(outcome)

    for batch in chunk_jobs(jobs):
        # A batch of one has nothing to amortize; skip the array contract and score it
        # directly. For 2+ jobs, one call scores them all; gaps fall back below.
        batch_scores: dict[int, ScoredPayload] = {}
        if len(batch) > 1:
            try:
                batch_scores = _score_batch_call(
                    provider, resume.extracted_text, batch, model, home_state
                )
            except Exception:  # noqa: BLE001 - a failed batch degrades to single-job, never lost
                batch_scores = {}

        for job in batch:
            try:
                payload = batch_scores.get(job.id)
                if payload is not None:
                    final = _persist_score(session, provider, job.id, payload)
                    _record(job, final, None)
                else:
                    result = score_one(
                        session, provider, resume.extracted_text, job, model=model
                    )
                    _record(job, result.score, None)
            except Exception as exc:  # noqa: BLE001 - one job's failure can't kill the run
                _record(job, None, str(exc))

    if low_scorers:
        services.bulk_set_status(session, low_scorers, ApplicationStatus.archived)
    return outcomes


def open_session() -> Session:
    """A fresh Session on the app engine. Isolated here so the background task can
    open its own handle (no cross-thread SQLite sharing) and tests can override it."""
    return Session(engine())
