"""Per-profile preferences: the ``AppSetting`` values edited on ``/settings``.

Each profile's value lives under ``contracts.profile_pref_key``; a profile
with no value of its own reads the pre-profile shared key, which is how a
migrated Default keeps the setting it had. The HTTP edge is
``api/preferences.py``.
"""

from __future__ import annotations

from typing import Optional

from sqlmodel import Session

from job_applier.contracts import (
    DEFAULT_GHOSTED_AFTER_DAYS,
    GHOSTED_AFTER_DAYS_KEY,
    profile_pref_key,
)
from job_applier.models.db import get_setting, set_setting
from job_applier.models.scoping import session_profile_id


def get_profile_pref(session: Session, key: str) -> Optional[str]:
    """This profile's value for preference ``key``, else the pre-profile shared
    value (which is how a migrated Default keeps its setting)."""
    pid = session_profile_id(session)
    if pid is not None:
        own = get_setting(session, profile_pref_key(pid, key))
        if own is not None:
            return own
    return get_setting(session, key)


def set_profile_pref(session: Session, key: str, value: str) -> None:
    pid = session_profile_id(session, create=True)
    set_setting(session, profile_pref_key(pid, key), value)


def ghosted_after_days(session: Session) -> int:
    """The configured ghost cut-off, or the default.

    ``AppSetting`` values are strings, so a row hand-edited to "" or "soon" would
    otherwise blow up every read of ``/followups``. An unparseable value falls
    back rather than raising: a broken preference should not take down the page
    it configures.
    """
    raw = get_profile_pref(session, GHOSTED_AFTER_DAYS_KEY)
    try:
        return int(raw) if raw is not None else DEFAULT_GHOSTED_AFTER_DAYS
    except ValueError:
        return DEFAULT_GHOSTED_AFTER_DAYS
