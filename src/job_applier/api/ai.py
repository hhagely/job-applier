"""AI router: provider substrate (detect installed CLIs, persist the selection,
round-trip a trivial prompt) plus the background AI task endpoints — score-pending,
generate-draft, draft-batch, and suggest-roles — that run the flows in
``job_applier.ai`` and are polled via ``GET /api/ai/tasks/{id}``.

The non-AI app works fully with zero providers detected: the provider-gated
endpoints return 409 rather than erroring, and everything else degrades gracefully.
"""

from __future__ import annotations

import functools

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from job_applier import services
from job_applier.ai import drafting, providers, scoring, suggest, tasks
from job_applier.api.deps import require_ai_ready
from job_applier.api.profile import profile_out
from job_applier.api.schemas import (
    AiTestIn,
    AiTestOut,
    DraftBatchIn,
    ProviderOut,
    ProvidersOut,
    ScorePendingIn,
    SearchProfileOut,
    SelectProviderIn,
    StartTaskOut,
    TaskOut,
)
from job_applier.ai.tasks import TaskState
from job_applier.models.db import JobPosting, get_session, get_setting, set_setting

router = APIRouter(prefix="/api/ai", tags=["ai"])

AI_PROVIDER_KEY = "ai_provider"
AI_MODEL_KEY = "ai_model"

# Fixed, side-effect-free prompt for the "Test" round-trip.
_TEST_PROMPT = "Respond with exactly the word: pong"


def _providers_out(session: Session) -> ProvidersOut:
    infos = providers.detect_all()
    selected = get_setting(session, AI_PROVIDER_KEY)
    # If the previously-selected provider is no longer available, don't report it
    # as selected (the UI should prompt for a new choice).
    available_names = {i.name for i in infos if i.available}
    if selected not in available_names:
        selected = None
    return ProvidersOut(
        providers=[
            ProviderOut(
                name=i.name,
                display_name=i.display_name,
                tier=i.tier,
                available=i.available,
                version=i.version,
            )
            for i in infos
        ],
        selected=selected,
        model=get_setting(session, AI_MODEL_KEY, providers.DEFAULT_OLLAMA_MODEL),
    )


@router.get("/providers", response_model=ProvidersOut)
def list_providers(session: Session = Depends(get_session)) -> ProvidersOut:
    return _providers_out(session)


@router.get("/selected")
def selected_provider(session: Session = Depends(get_session)) -> dict:
    """Cheap read of the persisted selection (no CLI detection). Used by the
    layout header so navigation doesn't spawn a `--version` probe each time."""
    return {"selected": get_setting(session, AI_PROVIDER_KEY)}


@router.put("/provider", response_model=ProvidersOut)
def select_provider(
    body: SelectProviderIn, session: Session = Depends(get_session)
) -> ProvidersOut:
    available = {i.name for i in providers.detect_all() if i.available}
    if body.name not in available:
        raise HTTPException(422, f"provider '{body.name}' is not available")
    set_setting(session, AI_PROVIDER_KEY, body.name)
    if body.model:
        set_setting(session, AI_MODEL_KEY, body.model)
    return _providers_out(session)


@router.post("/test", response_model=AiTestOut)
def test_provider(
    body: AiTestIn, session: Session = Depends(get_session)
) -> AiTestOut:
    selected = get_setting(session, AI_PROVIDER_KEY)
    if not selected:
        raise HTTPException(400, "no AI provider selected")
    prompt = (body.prompt or _TEST_PROMPT).strip() or _TEST_PROMPT
    model = get_setting(session, AI_MODEL_KEY)
    try:
        output = providers.run(selected, prompt, timeout=60, model=model)
        return AiTestOut(ok=True, output=output, error=None)
    except providers.ProviderError as exc:
        return AiTestOut(ok=False, output=None, error=str(exc))


def _run_score_pending(
    state: TaskState, *, provider: str, model: str | None, job_ids: list[int]
) -> None:
    """Worker body: score the resolved job ids on the task's own DB session,
    updating the progress state after each job."""

    def _cb(outcome: scoring.JobScoreOutcome) -> None:
        state.done += 1
        label = f"{outcome.score}/100" if outcome.score is not None else "ERROR"
        state.results.append(f"{outcome.job_id}  {label}  {outcome.title}")
        if outcome.error:
            state.errors.append(f"{outcome.job_id}: {outcome.error}")

    with scoring.open_session() as session:
        scoring.score_pending(
            session, provider=provider, model=model, job_ids=job_ids, progress_cb=_cb
        )


@router.post("/score-pending", response_model=StartTaskOut)
def start_score_pending(
    body: ScorePendingIn,
    session: Session = Depends(get_session),
    provider: str = Depends(require_ai_ready),
) -> StartTaskOut:
    pending = services.select_pending_jobs(
        session, limit=200, include_stale=body.include_stale
    )
    if body.job_ids:
        wanted = set(body.job_ids)
        pending = [j for j in pending if j.id in wanted]
    ids = [j.id for j in pending]

    model = get_setting(session, AI_MODEL_KEY)
    fn = functools.partial(
        _run_score_pending, provider=provider, model=model, job_ids=ids
    )
    task_id = tasks.start_task("score_pending", len(ids), fn)
    return StartTaskOut(task_id=task_id)


@router.get("/tasks/{task_id}", response_model=TaskOut)
def get_task_status(task_id: str) -> TaskOut:
    state = tasks.get_task(task_id)
    if state is None:
        raise HTTPException(404, "task not found")
    return TaskOut(
        id=state.id,
        kind=state.kind,
        total=state.total,
        done=state.done,
        status=state.status,
        errors=state.errors,
        results=state.results,
    )


# Stage -> progress count for the draft task (drafting/rendering/scoring/done).
_DRAFT_STAGES = {"drafting": 1, "rendering": 2, "scoring": 3, "done": 3}
DRAFT_TASK_STEPS = 3


def run_generate_draft_task(
    state: TaskState, *, provider: str, model: str | None, job_id: int
) -> None:
    """Worker body for a tailored-draft run on the task's own DB session."""

    def _cb(stage: str) -> None:
        state.done = _DRAFT_STAGES.get(stage, state.done)
        state.results.append(stage)

    with scoring.open_session() as session:
        job = session.get(JobPosting, job_id)
        if job is None:
            raise ValueError(f"job {job_id} not found")
        drafting.generate_draft(session, provider, job, model=model, progress_cb=_cb)


def _run_draft_batch(
    state: TaskState, *, provider: str, model: str | None, job_ids: list[int]
) -> None:
    """Worker body: draft each job in turn on the task's own DB session, advancing
    progress one step per job. One job's failure is recorded and the batch keeps
    going, matching how score-pending tolerates a bad job."""
    with scoring.open_session() as session:
        for job_id in job_ids:
            job = session.get(JobPosting, job_id)
            title = job.title if job is not None else f"job {job_id}"
            try:
                if job is None:
                    raise ValueError("job not found")
                drafting.generate_draft(session, provider, job, model=model)
                state.results.append(f"{job_id}  drafted  {title}")
            except Exception as exc:  # noqa: BLE001 - one bad job shouldn't kill the batch
                state.errors.append(f"{job_id}: {exc}")
                state.results.append(f"{job_id}  ERROR  {title}")
            finally:
                state.done += 1


@router.post("/draft-batch", response_model=StartTaskOut)
def start_draft_batch(
    body: DraftBatchIn,
    session: Session = Depends(get_session),
    provider: str = Depends(require_ai_ready),
) -> StartTaskOut:
    # Dedupe (preserving order) and keep only ids that resolve to a real job.
    ids = [
        jid
        for jid in dict.fromkeys(body.job_ids)
        if session.get(JobPosting, jid) is not None
    ]
    if not ids:
        raise HTTPException(400, "no valid jobs to draft")

    model = get_setting(session, AI_MODEL_KEY)
    fn = functools.partial(
        _run_draft_batch, provider=provider, model=model, job_ids=ids
    )
    task_id = tasks.start_task("draft_batch", len(ids), fn)
    return StartTaskOut(task_id=task_id)


@router.post("/suggest-roles", response_model=SearchProfileOut)
def suggest_roles_endpoint(
    session: Session = Depends(get_session),
    provider: str = Depends(require_ai_ready),
) -> SearchProfileOut:
    model = get_setting(session, AI_MODEL_KEY)
    try:
        profile = suggest.suggest_roles(session, provider, model=model)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        # Never surface an opaque 500 for this flow: it shells out to an external
        # CLI and does DB I/O over a long window, so map SuggestError,
        # ProviderError, and any unexpected failure (e.g. a transient DB lock) to
        # a 502 that carries the real reason instead of "Internal Server Error".
        raise HTTPException(502, f"suggestion failed: {exc}") from exc
    return profile_out(profile)
