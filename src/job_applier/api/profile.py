"""Search-profile endpoints: read the active hard-filter profile, replace it, and
stage/clear an LLM-generated recommendation draft (accepted via PUT, never
auto-applied).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from job_applier import services
from job_applier.api.schemas import (
    SearchProfileBody,
    SearchProfileOut,
    SearchProfileRecommendationIn,
)
from job_applier.filters import normalize_home_state
from job_applier.models.db import SearchProfile, get_session

router = APIRouter(tags=["search-profile"])

_load_or_create_profile = services.load_or_create_profile


def profile_out(p: Optional[SearchProfile]) -> SearchProfileOut:
    """Present a ``SearchProfile`` ORM row (or ``None``) as the API response DTO.

    Lives in the API layer because it produces an HTTP schema; the AI suggest
    endpoint reuses it so the profile response shape can't drift between routers.
    """
    if p is None:
        return SearchProfileOut(using_defaults=True)
    using_defaults = not p.required_tech or not p.seniority_terms
    return SearchProfileOut(
        id=p.id,
        role_titles=list(p.role_titles or []),
        seniority_terms=list(p.seniority_terms or []),
        required_tech=list(p.required_tech or []),
        excluded_tech=list(p.excluded_tech or []),
        extracted_skills=list(p.extracted_skills or []),
        home_state=p.home_state,
        recommendations_draft=p.recommendations_draft,
        updated_at=p.updated_at,
        using_defaults=using_defaults,
    )


_profile_out = profile_out


@router.get("/api/search-profile", response_model=SearchProfileOut)
def get_search_profile(session: Session = Depends(get_session)):
    p = session.exec(select(SearchProfile).order_by(SearchProfile.id)).first()
    return _profile_out(p)


@router.put("/api/search-profile", response_model=SearchProfileOut)
def put_search_profile(
    body: SearchProfileBody, session: Session = Depends(get_session)
):
    try:
        home_state = normalize_home_state(body.home_state)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    p = _load_or_create_profile(session)
    p.role_titles = body.role_titles
    p.seniority_terms = body.seniority_terms
    p.required_tech = body.required_tech
    p.excluded_tech = body.excluded_tech
    p.extracted_skills = body.extracted_skills
    p.home_state = home_state
    p.updated_at = datetime.now(timezone.utc)
    session.add(p)
    session.commit()
    session.refresh(p)
    return _profile_out(p)


@router.post("/api/search-profile/recommendations", response_model=SearchProfileOut)
def post_recommendations(
    body: SearchProfileRecommendationIn, session: Session = Depends(get_session)
):
    """Save an LLM-generated proposal as a draft on the profile.

    Does NOT mutate the active fields — the user reviews + accepts via PUT to
    apply. Overwrites any prior draft.
    """
    p = services.save_recommendations(session, body.model_dump())
    return _profile_out(p)


@router.delete("/api/search-profile/recommendations", response_model=SearchProfileOut)
def clear_recommendations(session: Session = Depends(get_session)):
    p = session.exec(select(SearchProfile).order_by(SearchProfile.id)).first()
    if p is None:
        return _profile_out(None)
    p.recommendations_draft = None
    p.updated_at = datetime.now(timezone.utc)
    session.add(p)
    session.commit()
    session.refresh(p)
    return _profile_out(p)
