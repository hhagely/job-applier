"""Search-profile lifecycle: which profile is active, and switching between them.

Several saved profiles, exactly one active. The active profile drives the hard
filter at ingest, scopes the queue / pending-match / stale-score adoption to the
postings it surfaced (``JobProfileLink``), and owns the active resume.

**Invariant:** the active profile's ``resume_id`` is the active resume
(``Resume.is_active``). Everything that scores or drafts reads
``Resume.is_active``, so rather than teach each of them about profiles, the two
places that can break the invariant keep it: ``activate_profile`` moves the
active resume to the profile's, and a resume upload (``adopt_uploaded_resume``)
points the active profile at the new file.

Depends only on the models so the filter, ingest, and the services layer can all
import it without a cycle.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Session, select

from job_applier.models.db import (
    JobProfileLink,
    Resume,
    SearchProfile,
    forget_active_profile,
    session_profile_id,
)


class ProfileError(ValueError):
    """A profile operation the caller asked for that can't be honored."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def active_profile(session: Session) -> Optional[SearchProfile]:
    """The profile this session works for, or ``None`` when none exists yet.

    Normally the active profile (oldest row if none is flagged, so a hand-built
    row still reads as the profile it obviously is). A background task pinned to
    a profile gets *that* one even after the user switches, which keeps its
    queue scoping, resume, and writes all on the same person.
    """
    pid = session_profile_id(session)
    return session.get(SearchProfile, pid) if pid is not None else None


def _active_resume_id(session: Session) -> Optional[int]:
    return session.exec(
        select(Resume.id).where(Resume.is_active == True)  # noqa: E712
    ).first()


def load_or_create_profile(session: Session) -> SearchProfile:
    """The active profile, creating an active "Default" one on a fresh install.

    Flushes but does not commit — the caller owns the transaction.
    """
    p = active_profile(session)
    if p is None:
        p = SearchProfile(
            name="Default", is_active=True, resume_id=_active_resume_id(session)
        )
        session.add(p)
        session.flush()
    return p


def list_profiles(session: Session) -> list[SearchProfile]:
    return list(session.exec(select(SearchProfile).order_by(SearchProfile.id)).all())


def get_profile(session: Session, profile_id: int) -> SearchProfile:
    p = session.get(SearchProfile, profile_id)
    if p is None:
        raise LookupError(f"search profile {profile_id} not found")
    return p


def _clean_name(name: str) -> str:
    name = name.strip()
    if not name:
        raise ProfileError("profile name can't be empty")
    return name


def create_profile(
    session: Session, *, name: str, clone_from: Optional[int] = None
) -> SearchProfile:
    """Add an inactive profile, optionally copying another's criteria + resume.

    A blank profile starts on the current active resume, so activating it never
    leaves the app without one.
    """
    p = SearchProfile(name=_clean_name(name), resume_id=_active_resume_id(session))
    if clone_from is not None:
        src = get_profile(session, clone_from)
        p.role_titles = list(src.role_titles or [])
        p.seniority_terms = list(src.seniority_terms or [])
        p.required_tech = list(src.required_tech or [])
        p.excluded_tech = list(src.excluded_tech or [])
        p.extracted_skills = list(src.extracted_skills or [])
        p.home_state = src.home_state
        p.resume_id = src.resume_id
    session.add(p)
    session.commit()
    session.refresh(p)
    return p


def _set_active_resume(session: Session, resume_id: int) -> None:
    if session.get(Resume, resume_id) is None:
        raise LookupError(f"resume {resume_id} not found")
    for r in session.exec(select(Resume).where(Resume.is_active == True)).all():  # noqa: E712
        if r.id != resume_id:
            r.is_active = False
            session.add(r)
    target = session.get(Resume, resume_id)
    target.is_active = True
    session.add(target)


def activate_profile(session: Session, profile_id: int) -> SearchProfile:
    """Make ``profile_id`` the active profile and its resume the active resume.

    A profile with no resume yet adopts the current one rather than leaving the
    app resume-less. Scores then read stale or fresh by the usual id comparison —
    switching back to a profile brings its scores back without re-running them.
    """
    target = get_profile(session, profile_id)
    for p in session.exec(
        select(SearchProfile).where(SearchProfile.is_active == True)  # noqa: E712
    ).all():
        if p.id != target.id:
            p.is_active = False
            session.add(p)
    target.is_active = True
    if target.resume_id is None:
        target.resume_id = _active_resume_id(session)
    elif session.get(Resume, target.resume_id) is not None:
        _set_active_resume(session, target.resume_id)
    target.updated_at = _now()
    session.add(target)
    session.commit()
    forget_active_profile(session)
    session.refresh(target)
    return target


def update_profile_meta(
    session: Session,
    profile_id: int,
    *,
    name: Optional[str] = None,
    resume_id: Optional[int] = None,
) -> SearchProfile:
    """Rename a profile and/or point it at a different (already uploaded) resume.

    Re-pointing the *active* profile switches the active resume with it, keeping
    the invariant.
    """
    p = get_profile(session, profile_id)
    if name is not None:
        p.name = _clean_name(name)
    if resume_id is not None:
        if session.get(Resume, resume_id) is None:
            raise LookupError(f"resume {resume_id} not found")
        p.resume_id = resume_id
        active = active_profile(session)
        if active is not None and active.id == p.id:
            _set_active_resume(session, resume_id)
    p.updated_at = _now()
    session.add(p)
    session.commit()
    session.refresh(p)
    return p


def delete_profile(session: Session, profile_id: int) -> None:
    """Delete an inactive profile and its job links.

    The postings themselves stay — an applied job is history regardless of which
    profile found it — and remain reachable under "All profiles".
    """
    p = get_profile(session, profile_id)
    active = active_profile(session)
    if active is not None and active.id == p.id:
        raise ProfileError("can't delete the active profile — switch to another first")
    for link in session.exec(
        select(JobProfileLink).where(JobProfileLink.search_profile_id == p.id)
    ).all():
        session.delete(link)
    session.delete(p)
    session.commit()


def adopt_uploaded_resume(session: Session, resume_id: int) -> None:
    """Point the active profile at a just-uploaded resume (caller commits).

    The upload route already made it the active resume; this is the other half
    of the invariant.
    """
    p = load_or_create_profile(session)
    p.resume_id = resume_id
    p.updated_at = _now()
    session.add(p)


def adopt_legacy_drafts() -> int:
    """Move pre-profile draft folders under the active profile (one-time, at
    startup). Returns how many moved. Opens its own session; no-op when there
    are none, so a fresh install never gets a profile created just for this."""
    from job_applier import drafts
    from job_applier.models.db import engine

    root = drafts.settings.applications_dir
    if not root.is_dir() or not any(
        e.is_dir() and e.name.isdigit() for e in root.iterdir()
    ):
        return 0
    with Session(engine()) as session:
        pid = session_profile_id(session, create=True)
        session.commit()
    return drafts.move_legacy_draft_dirs(pid)


def profile_job_ids(profile_id: int):
    """Subquery of posting ids linked to ``profile_id``, for ``IN (...)`` filters."""
    return select(JobProfileLink.job_id).where(
        JobProfileLink.search_profile_id == profile_id
    )
