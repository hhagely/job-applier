"""Search-profile endpoints: read the active hard-filter profile, replace it, and
stage/clear an LLM-generated recommendation draft (accepted via PUT, never
auto-applied).
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlmodel import Session, select

from job_applier.api import services
from job_applier.api.schemas import (
    SearchProfileBody,
    SearchProfileOut,
    SearchProfileRecommendationIn,
)
from job_applier.models.db import SearchProfile, get_session

router = APIRouter(tags=["search-profile"])

_profile_out = services.profile_out
_load_or_create_profile = services.load_or_create_profile


@router.get("/api/search-profile", response_model=SearchProfileOut)
def get_search_profile(session: Session = Depends(get_session)):
    p = session.exec(select(SearchProfile).order_by(SearchProfile.id)).first()
    return _profile_out(p)


@router.put("/api/search-profile", response_model=SearchProfileOut)
def put_search_profile(
    body: SearchProfileBody, session: Session = Depends(get_session)
):
    p = _load_or_create_profile(session)
    p.role_titles = body.role_titles
    p.seniority_terms = body.seniority_terms
    p.required_tech = body.required_tech
    p.excluded_tech = body.excluded_tech
    p.extracted_skills = body.extracted_skills
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
    p = services.save_recommendations(session, body)
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
