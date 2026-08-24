"""User preference endpoints: the tunables that live in the ``AppSetting``
key/value table and are edited on ``/settings``.

Separate from ``api/profile.py`` on purpose — a ``SearchProfile`` describes what
to *ingest* (roles, tech, home state) and is what ``/suggest-roles`` proposes
against, while these are app behaviour the user sets by hand and no AI flow ever
touches.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlmodel import Session

from job_applier.contracts import (
    DEFAULT_GHOSTED_AFTER_DAYS,
    GHOSTED_AFTER_DAYS_KEY,
)
from job_applier.api.schemas import PreferencesOut, PreferencesUpdate
from job_applier.models.db import get_session, get_setting, set_setting

router = APIRouter(tags=["preferences"])


def ghosted_after_days(session: Session) -> int:
    """The configured ghost cut-off, or the default.

    ``AppSetting`` values are strings, so a row hand-edited to "" or "soon" would
    otherwise blow up every read of ``/followups``. An unparseable value falls
    back rather than raising: a broken preference should not take down the page
    it configures.
    """
    raw = get_setting(session, GHOSTED_AFTER_DAYS_KEY)
    try:
        return int(raw) if raw is not None else DEFAULT_GHOSTED_AFTER_DAYS
    except ValueError:
        return DEFAULT_GHOSTED_AFTER_DAYS


def _preferences_out(session: Session) -> PreferencesOut:
    return PreferencesOut(ghosted_after_days=ghosted_after_days(session))


@router.get("/api/preferences", response_model=PreferencesOut)
def read_preferences(session: Session = Depends(get_session)):
    return _preferences_out(session)


@router.patch("/api/preferences", response_model=PreferencesOut)
def update_preferences(
    body: PreferencesUpdate, session: Session = Depends(get_session)
):
    """Partial update — an omitted field keeps its stored value.

    Bounds are enforced by ``PreferencesUpdate`` (422 on anything outside them),
    so nothing unparseable or absurd reaches the key/value table.
    """
    if body.ghosted_after_days is not None:
        set_setting(session, GHOSTED_AFTER_DAYS_KEY, str(body.ghosted_after_days))
    return _preferences_out(session)
