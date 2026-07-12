"""Search-profile suggestion: analyze the active resume and propose roles + tech.

Writes only ``recommendations_draft`` (via the shared service), never the live
filter fields — same guarantee as the `/suggest-roles` slash command.
"""

from __future__ import annotations

import json
from typing import Optional

from pydantic import BaseModel

from sqlmodel import select

from job_applier import services
from job_applier.ai import prompt_safety, providers
from job_applier.ai.templates import load_template
from job_applier.models.db import SearchProfile, Session


class SuggestError(Exception):
    """The provider output couldn't be parsed into a recommendation."""


class SuggestedProfile(BaseModel):
    """The recommendation payload the suggest flow validates provider output into.

    Mirrors the API's ``SearchProfileRecommendationIn`` on purpose, so this flow
    doesn't depend on the web layer. It is persisted verbatim (``model_dump``) as
    the profile's ``recommendations_draft`` via ``services.save_recommendations``.
    """

    role_titles: list[str] = []
    seniority_terms: list[str] = []
    required_tech: list[str] = []
    excluded_tech: list[str] = []
    extracted_skills: list[str] = []
    rationale: Optional[str] = None


def _current_profile_summary(profile: Optional[SearchProfile]) -> str:
    if profile is None:
        return "(none set)"
    return json.dumps(
        {
            "role_titles": list(profile.role_titles or []),
            "seniority_terms": list(profile.seniority_terms or []),
            "required_tech": list(profile.required_tech or []),
            "excluded_tech": list(profile.excluded_tech or []),
        }
    )


def build_suggest_prompt(resume_text: str, profile: Optional[SearchProfile]) -> str:
    nonce = prompt_safety.new_nonce()
    current = prompt_safety.clean_untrusted(_current_profile_summary(profile), nonce)
    return (
        load_template("suggest.md")
        .replace("{{RESUME_TEXT}}", resume_text.strip())
        .replace("{{NONCE}}", nonce)
        .replace("{{CURRENT_PROFILE}}", current)
    )


def _run_and_parse(
    provider: str, prompt: str, model: Optional[str]
) -> SuggestedProfile:
    return providers.run_json_or(
        provider,
        prompt,
        SuggestedProfile,
        error_cls=SuggestError,
        label="suggestion",
        model=model,
        nudge="IMPORTANT: return ONLY the JSON object described above.",
    )


def suggest_roles(
    session: Session,
    provider: str,
    *,
    model: Optional[str] = None,
) -> SearchProfile:
    """Analyze the active resume and save a recommendation draft. Returns the
    updated profile. Never mutates the live filter fields."""
    resume = services.active_resume(session)
    if resume is None:
        raise SuggestError("no active resume — upload one first")

    # Read the existing profile (if any) without creating a row just to describe it.
    profile = session.exec(select(SearchProfile).order_by(SearchProfile.id)).first()
    rec = _run_and_parse(
        provider, build_suggest_prompt(resume.extracted_text, profile), model
    )
    return services.save_recommendations(session, rec.model_dump())
