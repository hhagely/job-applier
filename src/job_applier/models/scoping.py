"""Profile scoping: every ORM Session reads and writes one profile's rows.

Importing this module installs the Session hooks; ``job_applier.models`` does
so on import, so any code that touches the models gets them.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Optional

from sqlalchemy import event
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session as SASession
from sqlalchemy.orm import with_loader_criteria
from sqlmodel import Session

from job_applier.models.db import (
    Application,
    BlacklistedCompany,
    JobProfileLink,
    MatchScore,
    MatchScoreHistory,
    Resume,
)

# ---- profile scoping --------------------------------------------------------
#
# The models in ``PROFILE_SCOPED`` belong to a profile.
# Rather than thread a profile id through every query, every ORM statement a
# Session runs is filtered to one profile (SQLAlchemy's documented multi-tenant
# pattern, ``with_loader_criteria``) and new rows are stamped with it on flush.
# That keeps ``JobPosting.application`` / ``.score`` single objects meaning "the
# current profile's", so the many call sites that read them don't change.
#
# Which profile, in order: ``session.info["profile_id"]`` when a caller pinned
# one; the ``current_profile_id`` ContextVar (background tasks set it, so a run
# keeps writing for the profile that started it even if the user switches); the
# active profile in the DB. Pass ``execution_options(all_profiles=True)`` for the
# rare query that must see every profile.

PROFILE_SCOPED = (
    Application,
    MatchScore,
    MatchScoreHistory,
    BlacklistedCompany,
    JobProfileLink,
    Resume,
)

current_profile_id: ContextVar[Optional[int]] = ContextVar(
    "current_profile_id", default=None
)

_ACTIVE_CACHE = "_active_profile_id"


def _active_profile_id_from(conn: Connection) -> Optional[int]:
    """The active profile on a raw connection (oldest row if none is flagged,
    matching ``profiles.active_profile``). Raw SQL so it can run inside ORM
    events without re-entering them."""
    row = conn.exec_driver_sql(
        "SELECT id FROM searchprofile ORDER BY is_active DESC, id LIMIT 1"
    ).first()
    return row[0] if row else None


def _insert_default_profile(conn: Connection) -> int:
    """Create the active "Default" profile on the active resume, if any. Raw SQL
    so it can run from ORM events and migrations; ``profiles.load_or_create_profile``
    is the ORM path and keeps the same shape."""
    return conn.exec_driver_sql(
        "INSERT INTO searchprofile (name, is_active, resume_id, role_titles, title_terms, "
        "seniority_terms, required_tech, excluded_tech, extracted_skills, updated_at) "
        "VALUES ('Default', 1, (SELECT id FROM resume WHERE is_active = 1 LIMIT 1), "
        "'[]', '[]', '[]', '[]', '[]', '[]', CURRENT_TIMESTAMP)"
    ).lastrowid





def session_profile_id(session: Session, *, create: bool = False) -> Optional[int]:
    """The profile this session reads and writes. ``create`` makes a Default
    profile on a fresh install, for the flush that needs somewhere to put a row."""
    pinned = session.info.get("profile_id")
    if pinned is not None:
        return pinned
    ctx = current_profile_id.get()
    if ctx is not None:
        return ctx
    cached = session.info.get(_ACTIVE_CACHE)
    if cached is not None:
        return cached
    conn = session.connection()
    pid = _active_profile_id_from(conn)
    if pid is None and create:
        pid = _insert_default_profile(conn)
    if pid is not None:
        session.info[_ACTIVE_CACHE] = pid
    return pid


def forget_active_profile(session: Session) -> None:
    """Drop the session's cached active profile (after switching profiles)."""
    session.info.pop(_ACTIVE_CACHE, None)


@event.listens_for(SASession, "do_orm_execute")
def _scope_to_profile(state) -> None:  # noqa: ANN001
    if not (state.is_select or state.is_update or state.is_delete):
        return
    if state.is_column_load or state.execution_options.get("all_profiles"):
        return
    pid = session_profile_id(state.session)
    if pid is None:
        return
    state.statement = state.statement.options(
        *(
            with_loader_criteria(model, lambda cls: cls.profile_id == pid, include_aliases=True)
            for model in PROFILE_SCOPED
        )
    )


@event.listens_for(SASession, "before_flush")
def _stamp_profile(session, _flush_context, _instances) -> None:  # noqa: ANN001
    pending = [
        obj
        for obj in session.new
        if isinstance(obj, PROFILE_SCOPED) and obj.profile_id is None
    ]
    if pending:
        pid = session_profile_id(session, create=True)
        for obj in pending:
            obj.profile_id = pid
