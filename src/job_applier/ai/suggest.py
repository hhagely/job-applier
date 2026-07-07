"""Search-profile suggestion: analyze the active resume and propose roles + tech.

Writes only ``recommendations_draft`` (via the shared service), never the live
filter fields — same guarantee as the `/suggest-roles` slash command.
"""

from __future__ import annotations

import json
from importlib import resources
from typing import Optional

from pydantic import ValidationError

from sqlmodel import select

from job_applier.ai import providers
from job_applier.api import services
from job_applier.api.schemas import SearchProfileRecommendationIn
from job_applier.models.db import SearchProfile, Session


class SuggestError(Exception):
    """The provider output couldn't be parsed into a recommendation."""


_template_cache: Optional[str] = None


def _template() -> str:
    global _template_cache
    if _template_cache is None:
        _template_cache = (
            resources.files("job_applier.ai")
            .joinpath("prompts/suggest.md")
            .read_text(encoding="utf-8")
        )
    return _template_cache


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
    return (
        _template()
        .replace("{{RESUME_TEXT}}", resume_text.strip())
        .replace("{{CURRENT_PROFILE}}", _current_profile_summary(profile))
    )


def _run_and_parse(
    provider: str, prompt: str, model: Optional[str]
) -> SearchProfileRecommendationIn:
    last_err: Optional[Exception] = None
    for attempt in range(2):
        text = prompt
        if attempt == 1:
            text += "\n\nIMPORTANT: return ONLY the JSON object described above."
        raw = providers.run(provider, text, expect_json=True, model=model)
        try:
            data = providers.extract_json(raw)
            return SearchProfileRecommendationIn.model_validate(data)
        except (ValueError, ValidationError) as exc:
            last_err = exc
    raise SuggestError(f"invalid suggestion JSON after retry: {last_err}")


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
    return services.save_recommendations(session, rec)
