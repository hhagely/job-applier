"""Search-profile lifecycle: which profile is active, and switching between them.

Several saved profiles, exactly one active. Every profile's own rules run in
``matching`` after each scrape, recording a per-profile verdict as a
``JobProfileLink``; the active profile scopes the queue / pending-match /
stale-score adoption to its non-dropped verdicts (``queue_job_ids``) and owns
the active resume.

Each profile owns its resumes (``Resume`` is profile-scoped) and uses one of
them, ``SearchProfile.resume_id``; a new blank profile has none until its person
uploads one.

**Invariant:** the active profile's ``resume_id`` is the active resume
(``Resume.is_active``). Every reader goes through ``active_resume``, which
resolves via the profile; ``set_active_resume`` is the only writer of the flag,
called by ``activate_profile``, ``update_profile_meta`` and the resume upload
(which also calls ``adopt_uploaded_resume`` to point the profile at the file).

Depends only on the models so the filter, ingest, and the services layer can all
import it without a cycle.
"""

from __future__ import annotations

import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy import delete, update
from sqlmodel import Session, select
from sqlmodel.sql.expression import SelectOfScalar

from job_applier.models.db import (
    PROFILE_SCOPED,
    Application,
    ApplicationStatus,
    AppSetting,
    FilterStatus,
    JobProfileLink,
    Resume,
    SearchProfile,
    forget_active_profile,
    session_profile_id,
)


log = logging.getLogger(__name__)


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


def _flagged_resume_id(session: Session) -> Optional[int]:
    return session.exec(
        select(Resume.id).where(Resume.is_active == True)  # noqa: E712
    ).first()


def active_resume(session: Session) -> Optional[Resume]:
    """The resume this session's profile scores and drafts with — the one
    reader of "which resume is active" (staleness, scoring, drafting).

    Resolved through the profile rather than ``Resume.is_active`` alone, so a
    background task pinned to one person keeps using their resume even if the
    user switches profiles (which moves ``is_active``) mid-run. Falls back to the
    flagged row when the profile has no resume on file.
    """
    profile = active_profile(session)
    if profile is not None and profile.resume_id is not None:
        resume = session.get(Resume, profile.resume_id)
        if resume is not None:
            return resume
    return session.exec(
        select(Resume).where(Resume.is_active == True)  # noqa: E712
    ).first()


def active_resume_id(session: Session) -> Optional[int]:
    resume = active_resume(session)
    return resume.id if resume is not None else None


def load_or_create_profile(session: Session) -> SearchProfile:
    """The active profile, creating an active "Default" one on a fresh install.

    Flushes but does not commit — the caller owns the transaction.
    """
    p = active_profile(session)
    if p is None:
        p = SearchProfile(
            name="Default", is_active=True, resume_id=_flagged_resume_id(session)
        )
        session.add(p)
        session.flush()
    elif p.resume_id is None:
        # Made by the flush that saved its first resume, before that row existed.
        p.resume_id = _flagged_resume_id(session) or _newest_resume_id(session, p.id)
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


def owned_resume(session: Session, profile_id: int, resume_id: int) -> Optional[Resume]:
    """``resume_id`` if ``profile_id`` owns it, whichever profile is active."""
    return session.exec(
        select(Resume)
        .where(Resume.id == resume_id, Resume.profile_id == profile_id)
        .execution_options(all_profiles=True)
    ).first()


# Statuses that mean a profile actually did something with a posting; ``new`` is
# the untouched default and ``archived`` is the machine's "never pursued" bucket.
_UNTOUCHED = (ApplicationStatus.new, ApplicationStatus.archived)


def other_profile_statuses(
    session: Session, job_id: int
) -> list[tuple[int, str, ApplicationStatus]]:
    """``(profile_id, name, status)`` for every *other* profile that has acted on
    ``job_id``, oldest profile first."""
    rows = session.exec(
        select(Application.profile_id, SearchProfile.name, Application.status)
        .join(SearchProfile, SearchProfile.id == Application.profile_id)  # type: ignore[arg-type]
        .where(Application.job_id == job_id)
        .where(Application.profile_id != session_profile_id(session))
        .where(Application.status.not_in(_UNTOUCHED))  # type: ignore[attr-defined]
        .order_by(SearchProfile.id)
        .execution_options(all_profiles=True)
    ).all()
    return [(pid, name, ApplicationStatus(status)) for pid, name, status in rows]


def resume_filenames(session: Session) -> dict[int, str]:
    """Each profile's in-use resume filename, keyed by profile id."""
    rows = session.exec(
        select(SearchProfile.id, Resume.original_filename)
        .join(Resume, Resume.id == SearchProfile.resume_id)  # type: ignore[arg-type]
        .where(Resume.profile_id == SearchProfile.id)
        .execution_options(all_profiles=True)
    ).all()
    return {pid: name for pid, name in rows}


def _newest_resume_id(session: Session, profile_id: int) -> Optional[int]:
    return session.exec(
        select(Resume.id)
        .where(Resume.profile_id == profile_id)
        .order_by(Resume.uploaded_at.desc(), Resume.id.desc())  # type: ignore[union-attr]
        .execution_options(all_profiles=True)
    ).first()


def create_profile(
    session: Session, *, name: str, clone_from: Optional[int] = None
) -> SearchProfile:
    """Add an inactive profile, optionally copying another's criteria + resume.

    A blank profile is a new person: it starts with no resume. A copy gets its
    own row for the source's resume (same PDF), since profiles never share one.
    """
    p = SearchProfile(name=_clean_name(name))
    src_resume: Optional[Resume] = None
    if clone_from is not None:
        src = get_profile(session, clone_from)
        p.role_titles = list(src.role_titles or [])
        p.seniority_terms = list(src.seniority_terms or [])
        p.required_tech = list(src.required_tech or [])
        p.excluded_tech = list(src.excluded_tech or [])
        p.extracted_skills = list(src.extracted_skills or [])
        p.home_state = src.home_state
        if src.resume_id is not None:
            src_resume = owned_resume(session, src.id, src.resume_id)
    session.add(p)
    session.flush()
    if src_resume is not None:
        copy = Resume(
            profile_id=p.id,
            original_filename=src_resume.original_filename,
            pdf_path=src_resume.pdf_path,
            extracted_text=src_resume.extracted_text,
            page_count=src_resume.page_count,
            uploaded_at=src_resume.uploaded_at,
        )
        session.add(copy)
        session.flush()
        p.resume_id = copy.id
        session.add(p)
    session.commit()
    session.refresh(p)
    return p


def set_active_resume(session: Session, resume_id: Optional[int]) -> None:
    """Flag ``resume_id`` as the active resume and demote every other row, in
    every profile (caller commits). ``None`` leaves no resume flagged — a profile
    that hasn't uploaded one. The only writer of ``Resume.is_active``."""
    if resume_id is not None and session.exec(
        select(Resume.id).where(Resume.id == resume_id).execution_options(all_profiles=True)
    ).first() is None:
        raise LookupError(f"resume {resume_id} not found")
    session.execute(
        update(Resume)
        .where(Resume.is_active == True)  # noqa: E712
        .values(is_active=False)
        .execution_options(all_profiles=True, synchronize_session=False)
    )
    if resume_id is not None:
        session.execute(
            update(Resume)
            .where(Resume.id == resume_id)
            .values(is_active=True)
            .execution_options(all_profiles=True, synchronize_session=False)
        )
    session.expire_all()


def activate_profile(session: Session, profile_id: int) -> SearchProfile:
    """Make ``profile_id`` the active profile and its resume the active resume.

    A profile whose resume is gone falls back to its newest upload; one with
    none leaves no resume active, so the app asks that person to upload theirs.
    Scores then read stale or fresh by the usual id comparison — switching back
    to a profile brings its scores back without re-running them.
    """
    target = get_profile(session, profile_id)
    for p in session.exec(
        select(SearchProfile).where(SearchProfile.is_active == True)  # noqa: E712
    ).all():
        if p.id != target.id:
            p.is_active = False
            session.add(p)
    target.is_active = True
    if target.resume_id is None or owned_resume(session, target.id, target.resume_id) is None:
        target.resume_id = _newest_resume_id(session, target.id)
    set_active_resume(session, target.resume_id)
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
    """Rename a profile and/or switch it to another of its own resumes.

    Re-pointing the *active* profile switches the active resume with it, keeping
    the invariant.
    """
    p = get_profile(session, profile_id)
    if name is not None:
        p.name = _clean_name(name)
    if resume_id is not None:
        if owned_resume(session, p.id, resume_id) is None:
            raise LookupError(f"resume {resume_id} not found for this profile")
        p.resume_id = resume_id
        active = active_profile(session)
        if active is not None and active.id == p.id:
            set_active_resume(session, resume_id)
    p.updated_at = _now()
    session.add(p)
    session.commit()
    session.refresh(p)
    return p


def delete_profile(session: Session, profile_id: int) -> None:
    """Delete an inactive profile and everything that is only its: verdicts,
    statuses + notes, scores + history, blacklist, preferences, resumes, and
    tailored drafts on disk. Shared postings stay.

    Profiles can be different people, so a profile's rows are that person's
    data; leaving them behind would orphan rows pointing at a deleted profile.
    """
    from job_applier import drafts

    p = get_profile(session, profile_id)
    active = active_profile(session)
    if active is not None and active.id == p.id:
        raise ProfileError("can't delete the active profile — switch to another first")
    # Move the drafts aside first, so a reused id never finds them even if the
    # delete below fails (a PDF open in a viewer blocks it on Windows).
    folder = drafts.settings.applications_dir / f"profile-{p.id}"
    doomed = folder.with_name(f"{folder.name}.deleted")
    if folder.exists():
        shutil.rmtree(doomed, ignore_errors=True)
        folder.rename(doomed)
    pdfs = set(
        session.exec(
            select(Resume.pdf_path)
            .where(Resume.profile_id == p.id)
            .execution_options(all_profiles=True)
        ).all()
    )
    for model in PROFILE_SCOPED:
        session.execute(
            delete(model)
            .where(model.profile_id == p.id)
            .execution_options(all_profiles=True)
        )
    session.execute(delete(AppSetting).where(AppSetting.key.startswith(f"pref:{p.id}:")))  # type: ignore[union-attr]
    session.delete(p)
    session.commit()
    # A copied profile's resume rows share the PDF; only remove files nobody
    # else's row still points at, and only inside the resumes folder.
    still_used = set(
        session.exec(
            select(Resume.pdf_path)
            .where(Resume.pdf_path.in_(pdfs))  # type: ignore[attr-defined]
            .execution_options(all_profiles=True)
        ).all()
    )
    resumes_dir = drafts.settings.resumes_dir.resolve()
    for pdf in pdfs - still_used:
        path = Path(pdf).resolve()
        if path.is_relative_to(resumes_dir):
            try:
                path.unlink(missing_ok=True)
            except OSError as e:
                log.warning("couldn't remove deleted profile's resume %s: %s", path, e)
    if doomed.exists():
        shutil.rmtree(
            doomed,
            onexc=lambda _fn, path, exc: log.warning(
                "couldn't remove deleted profile's draft %s: %s", path, exc
            ),
        )


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


def queue_job_ids(
    session: Session, status: Optional[FilterStatus] = None
) -> SelectOfScalar[int]:
    """Subquery of the posting ids in this session's profile's queue, for
    ``IN (...)`` filters: its verdict is ``status``, or anything but ``dropped``
    when ``status`` is None. Postings the profile hasn't been matched against, or
    that its rules dropped, are never in its queue."""
    stmt = select(JobProfileLink.job_id).where(
        JobProfileLink.profile_id == session_profile_id(session)
    )
    if status is None:
        return stmt.where(JobProfileLink.filter_status != FilterStatus.dropped)
    return stmt.where(JobProfileLink.filter_status == status)
