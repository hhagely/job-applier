"""AI router: provider substrate (detect installed CLIs, persist the selection,
round-trip a trivial prompt) plus the background AI task endpoints — score-pending,
generate-draft, draft-batch, and suggest-roles — that run the flows in
``job_applier.ai`` and are polled via ``GET /api/ai/tasks/{id}``.

The non-AI app works fully with zero providers detected: the provider-gated
endpoints return 409 rather than erroring, and everything else degrades gracefully.
"""

from __future__ import annotations

import asyncio
import functools
import json
from typing import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlmodel import Session
from starlette.responses import StreamingResponse

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
# Persisted override for the baseline (bulk) scoring model. When unset, the resolver
# falls back to the provider's built-in lighter default, then the generation model.
AI_SCORING_MODEL_KEY = "ai_scoring_model"

# Fixed, side-effect-free prompt for the "Test" round-trip.
_TEST_PROMPT = "Respond with exactly the word: pong"


def resolve_scoring_model(session: Session, provider: str) -> str | None:
    """The model to use for baseline (bulk) scoring: the user's persisted override,
    else the provider's lighter built-in default (Sonnet on Claude), else the
    configured generation model (finally the account default when all are unset).

    Baseline scoring is high-volume triage, so it defaults to a cheaper tier than
    drafting/tailored re-scoring, which keep the configured generation model."""
    override = get_setting(session, AI_SCORING_MODEL_KEY)
    if override:
        return override
    default = providers.default_scoring_model(provider)
    if default:
        return default
    return get_setting(session, AI_MODEL_KEY)


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
        scoring_model=get_setting(session, AI_SCORING_MODEL_KEY),
        scoring_model_default=(
            providers.default_scoring_model(selected) if selected else None
        ),
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
    # ``scoring_model`` present (even as "") means the user submitted the field: store
    # it, where "" clears the override so the resolver reverts to the provider default.
    if body.scoring_model is not None:
        set_setting(session, AI_SCORING_MODEL_KEY, body.scoring_model.strip())
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
        state.publish()

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
    # Dedupe: the single-worker executor would only queue a second run behind the
    # first, re-scanning the same pending queue and burning AI calls. If a
    # score-pending run is already in flight, hand the caller its id so the UI
    # re-attaches to that run instead of starting a duplicate.
    existing = tasks.active_task("score_pending")
    if existing is not None:
        return StartTaskOut(task_id=existing.id)

    pending = services.select_pending_jobs(
        session, limit=200, include_stale=body.include_stale
    )
    if body.job_ids:
        wanted = set(body.job_ids)
        pending = [j for j in pending if j.id in wanted]
    ids = [j.id for j in pending]

    # Baseline scoring uses the (cheaper) scoring model, not the generation model that
    # drafting/tailored re-scoring use.
    model = resolve_scoring_model(session, provider)
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
        ref=state.ref,
    )


# Seconds between keepalive comments on an idle stream. Keeps proxies / the
# browser from reaping a connection that has had no task activity for a while.
_SSE_KEEPALIVE_SECS = 20.0


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


@router.get("/events")
async def task_events(request: Request) -> StreamingResponse:
    """Server-Sent Events stream of *every* background task's progress.

    The client opens one long-lived ``EventSource`` at app load; the store on the
    far end reduces these snapshots into per-task state, so progress survives
    navigation and is visible from any page. On connect we replay the snapshots of
    any currently-running task so a fresh or reconnected client re-attaches
    without a poll. Workers publish from the task thread; we bridge each snapshot
    onto this request's event loop with ``call_soon_threadsafe``.
    """
    loop = asyncio.get_running_loop()
    queue: "asyncio.Queue[dict]" = asyncio.Queue()

    def _forward(event: dict) -> None:
        # Runs on the worker thread — hop back to the event loop thread-safely.
        loop.call_soon_threadsafe(queue.put_nowait, event)

    # Subscribe BEFORE replaying so any snapshot published during replay is
    # buffered on the queue rather than lost in the gap.
    tasks.subscribe(_forward)

    async def gen() -> AsyncIterator[str]:
        try:
            for snap in tasks.active_snapshots():
                yield _sse(snap)
            yield ": connected\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(
                        queue.get(), timeout=_SSE_KEEPALIVE_SECS
                    )
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                yield _sse(event)
        finally:
            tasks.unsubscribe(_forward)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # Disable proxy buffering (nginx) so events flush immediately.
            "X-Accel-Buffering": "no",
        },
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
        state.publish()

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
                state.publish()


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
