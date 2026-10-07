"""User preference endpoints: the tunables that live in the ``AppSetting``
key/value table and are edited on ``/settings``. Per profile — see
``contracts.profile_pref_key``.

Separate from ``api/profile.py`` on purpose — a ``SearchProfile`` describes what
to *ingest* (roles, tech, home state) and is what ``/suggest-roles`` proposes
against, while these are app behaviour the user sets by hand and no AI flow ever
touches.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlmodel import Session

from job_applier import preferences
from job_applier.api.schemas import PreferencesOut, PreferencesUpdate
from job_applier.contracts import GHOSTED_AFTER_DAYS_KEY
from job_applier.models.db import get_session

router = APIRouter(tags=["preferences"])


def _preferences_out(session: Session) -> PreferencesOut:
    return PreferencesOut(ghosted_after_days=preferences.ghosted_after_days(session))


@router.get("/api/preferences", response_model=PreferencesOut)
def read_preferences(session: Session = Depends(get_session)) -> PreferencesOut:
    return _preferences_out(session)


@router.patch("/api/preferences", response_model=PreferencesOut)
def update_preferences(
    body: PreferencesUpdate, session: Session = Depends(get_session)
) -> PreferencesOut:
    """Partial update — an omitted field keeps its stored value.

    Bounds are enforced by ``PreferencesUpdate`` (422 on anything outside them),
    so nothing unparseable or absurd reaches the key/value table.
    """
    if body.ghosted_after_days is not None:
        preferences.set_profile_pref(session, GHOSTED_AFTER_DAYS_KEY, str(body.ghosted_after_days))
    return _preferences_out(session)
