"""Search-profile endpoints.

``/api/search-profiles`` manages the saved profiles (list, create, rename /
re-point at a resume, delete, activate). The singular ``/api/search-profile``
routes read and replace the *active* profile's criteria and stage/clear its
LLM-generated recommendation draft (accepted via PUT, never auto-applied) —
they predate multiple profiles, and the legacy ``/suggest-roles`` command still
calls them.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from job_applier import matching, profiles, services
from job_applier.api.schemas import (
    SearchProfileBody,
    SearchProfileCreate,
    SearchProfileMetaUpdate,
    SearchProfileOut,
    SearchProfileRecommendationIn,
)
from job_applier.filters import normalize_home_state
from job_applier.models.db import SearchProfile, get_session

router = APIRouter(tags=["search-profile"])

_load_or_create_profile = services.load_or_create_profile


def profile_out(
    p: Optional[SearchProfile], *, is_active: Optional[bool] = None
) -> SearchProfileOut:
    """Present a ``SearchProfile`` ORM row (or ``None``) as the API response DTO.

    Lives in the API layer because it produces an HTTP schema; the AI suggest
    endpoint reuses it so the profile response shape can't drift between routers.
    ``is_active`` defaults to true: every caller but the list hands over the
    active profile, and ``active_profile`` may have picked an unflagged row.
    """
    if p is None:
        return SearchProfileOut(using_defaults=True)
    using_defaults = not p.required_tech or not p.seniority_terms
    return SearchProfileOut(
        id=p.id,
        name=p.name,
        is_active=True if is_active is None else is_active,
        resume_id=p.resume_id,
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


@router.get("/api/search-profiles", response_model=list[SearchProfileOut])
def list_search_profiles(session: Session = Depends(get_session)):
    active = profiles.active_profile(session)
    active_id = active.id if active else None
    return [
        _profile_out(p, is_active=p.id == active_id)
        for p in profiles.list_profiles(session)
    ]


@router.post("/api/search-profiles", response_model=SearchProfileOut, status_code=201)
def create_search_profile(
    body: SearchProfileCreate, session: Session = Depends(get_session)
):
    # Make sure the pre-existing setup has a row before adding a second, so the
    # current criteria stay the active profile rather than the new blank one.
    _load_or_create_profile(session)
    session.commit()
    try:
        p = profiles.create_profile(session, name=body.name, clone_from=body.clone_from)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except profiles.ProfileError as exc:
        raise HTTPException(422, str(exc)) from exc
    # A new person gets a full queue from what's already stored: no scrape.
    matching.start_rematch(p.id)
    return _profile_out(p, is_active=False)


@router.patch("/api/search-profiles/{profile_id}", response_model=SearchProfileOut)
def update_search_profile_meta(
    profile_id: int,
    body: SearchProfileMetaUpdate,
    session: Session = Depends(get_session),
):
    try:
        p = profiles.update_profile_meta(
            session, profile_id, name=body.name, resume_id=body.resume_id
        )
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except profiles.ProfileError as exc:
        raise HTTPException(422, str(exc)) from exc
    active = profiles.active_profile(session)
    return _profile_out(p, is_active=active is not None and active.id == p.id)


@router.delete("/api/search-profiles/{profile_id}", status_code=204)
def delete_search_profile(profile_id: int, session: Session = Depends(get_session)):
    try:
        profiles.delete_profile(session, profile_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except profiles.ProfileError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post(
    "/api/search-profiles/{profile_id}/activate", response_model=SearchProfileOut
)
def activate_search_profile(profile_id: int, session: Session = Depends(get_session)):
    try:
        p = profiles.activate_profile(session, profile_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    return _profile_out(p)


@router.get("/api/search-profile", response_model=SearchProfileOut)
def get_search_profile(session: Session = Depends(get_session)):
    return _profile_out(profiles.active_profile(session))


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
    matching.start_rematch(p.id)
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
    p = profiles.active_profile(session)
    if p is None:
        return _profile_out(None)
    p.recommendations_draft = None
    p.updated_at = datetime.now(timezone.utc)
    session.add(p)
    session.commit()
    session.refresh(p)
    return _profile_out(p)
