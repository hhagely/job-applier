"""AI provider endpoints: detect installed CLIs, persist the selection, and
round-trip a trivial prompt through the sandboxed runner.

No scoring/drafting here — this router just proves the provider substrate. The
non-AI app works fully with zero providers detected (Finding 7): every endpoint
degrades gracefully instead of erroring.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from job_applier.ai import providers
from job_applier.api.schemas import (
    AiTestIn,
    AiTestOut,
    ProviderOut,
    ProvidersOut,
    SelectProviderIn,
)
from job_applier.models.db import get_session, get_setting, set_setting

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
