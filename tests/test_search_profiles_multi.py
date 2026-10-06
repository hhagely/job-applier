"""Multiple search profiles: switching, the active-resume invariant, and the
per-profile scoping of ingest, the queue, and pending-match.

The invariant under test throughout: the active profile's ``resume_id`` is the
active resume, so everything that scores or drafts (which reads
``Resume.is_active``) follows the profile without knowing about it.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from job_applier import profiles, services
from job_applier.api.app import app
from job_applier.models import JobPosting, JobProfileLink, Resume, SearchProfile
from job_applier.models.db import get_session
from job_applier.sources.base import RawJob


def _raw(**overrides) -> RawJob:
    defaults = dict(
        source="test",
        source_id="t-1",
        url="https://example.com/jobs/1",
        title="Senior Software Engineer",
        company_name="Acme",
        description="We use TypeScript and React on Node.js.",
        location="Remote - US",
        remote=True,
    )
    defaults.update(overrides)
    return RawJob(**defaults)


@pytest.fixture
def engine():
    eng = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(eng)
    return eng


@pytest.fixture
def session(engine):
    with Session(engine) as s:
        yield s


@pytest.fixture
def client(engine):
    def _session_dep():
        with Session(engine) as s:
            yield s

    app.dependency_overrides[get_session] = _session_dep
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _resume(session: Session, name: str, *, active: bool = False) -> Resume:
    r = Resume(original_filename=name, pdf_path=f"/tmp/{name}", extracted_text="x", is_active=active)
    session.add(r)
    session.commit()
    session.refresh(r)
    return r


def _active_resume_id(session: Session) -> int | None:
    session.expire_all()
    r = services.active_resume(session)
    return r.id if r else None


# ---------------------------------------------------------------------------
# Lifecycle + the active-resume invariant
# ---------------------------------------------------------------------------


def test_activating_a_profile_switches_the_active_resume_and_back(session):
    ic = _resume(session, "ic.pdf", active=True)
    a = profiles.load_or_create_profile(session)
    session.commit()
    assert a.resume_id == ic.id

    b = profiles.create_profile(session, name="Manager")
    mgr = _resume(session, "mgr.pdf")
    profiles.update_profile_meta(session, b.id, resume_id=mgr.id)
    # Re-pointing an inactive profile leaves the active resume alone.
    assert _active_resume_id(session) == ic.id

    profiles.activate_profile(session, b.id)
    assert profiles.active_profile(session).id == b.id
    assert _active_resume_id(session) == mgr.id

    profiles.activate_profile(session, a.id)
    assert _active_resume_id(session) == ic.id
    assert session.exec(
        select(SearchProfile).where(SearchProfile.is_active == True)  # noqa: E712
    ).all() == [profiles.active_profile(session)]


@pytest.mark.parametrize("dangling", [False, True])
def test_activating_a_profile_without_a_usable_resume_adopts_the_current_one(
    session, dangling
):
    # No resume (or one whose row is gone) must not leave the profile and
    # Resume.is_active disagreeing: the profile adopts the current resume.
    ic = _resume(session, "ic.pdf", active=True)
    profiles.load_or_create_profile(session)
    session.commit()
    b = SearchProfile(name="B", resume_id=999 if dangling else None)
    session.add(b)
    session.commit()

    profiles.activate_profile(session, b.id)
    assert b.resume_id == ic.id
    assert _active_resume_id(session) == ic.id
    flagged = session.exec(select(Resume).where(Resume.is_active == True)).all()  # noqa: E712
    assert [r.id for r in flagged] == [ic.id]


def test_uploaded_resume_becomes_the_active_profiles_resume(session):
    _resume(session, "old.pdf", active=True)
    p = profiles.load_or_create_profile(session)
    session.commit()
    new = _resume(session, "new.pdf")
    profiles.adopt_uploaded_resume(session, new.id)
    session.commit()
    session.refresh(p)
    assert p.resume_id == new.id


def test_clone_copies_criteria_and_resume(session):
    r = _resume(session, "r.pdf", active=True)
    src = profiles.load_or_create_profile(session)
    src.required_tech = ["rust"]
    src.home_state = "Missouri"
    session.commit()
    clone = profiles.create_profile(session, name="Copy", clone_from=src.id)
    assert clone.required_tech == ["rust"]
    assert clone.home_state == "Missouri"
    assert clone.resume_id == r.id
    assert clone.is_active is False


def test_api_refuses_to_delete_the_active_profile(client):
    first = client.post("/api/search-profiles", json={"name": "Second"}).json()
    listed = client.get("/api/search-profiles").json()
    assert [p["name"] for p in listed] == ["Default", "Second"]
    active = next(p for p in listed if p["is_active"])
    assert active["name"] == "Default"

    assert client.delete(f"/api/search-profiles/{active['id']}").status_code == 409
    assert client.delete(f"/api/search-profiles/{first['id']}").status_code == 204
    assert [p["name"] for p in client.get("/api/search-profiles").json()] == ["Default"]


def test_singular_endpoint_edits_the_active_profile(client):
    second = client.post("/api/search-profiles", json={"name": "Second"}).json()
    client.post(f"/api/search-profiles/{second['id']}/activate")
    body = client.put(
        "/api/search-profile",
        json={"seniority_terms": ["staff"], "required_tech": ["go"]},
    ).json()
    assert body["id"] == second["id"]
    assert body["name"] == "Second"


# ---------------------------------------------------------------------------
# Scoping: pending-match + queue
# ---------------------------------------------------------------------------


def _queued(session: Session, profile_id: int, job_id: int) -> None:
    """Matching gave ``job_id`` a passed verdict for ``profile_id``."""
    session.add(JobProfileLink(job_id=job_id, profile_id=profile_id))


def _job(session: Session, source_id: str, title: str) -> int:
    j = JobPosting(
        source="test",
        source_id=source_id,
        url=f"https://example.com/{source_id}",
        title=title,
        description="TypeScript.",
        dedupe_hash=f"h-{source_id}",
    )
    session.add(j)
    session.flush()
    return j.id


def test_pending_match_and_queue_are_scoped_to_the_active_profile(session, client):
    a = profiles.load_or_create_profile(session)
    session.commit()
    b = profiles.create_profile(session, name="B")
    _queued(session, a.id, _job(session, "t-1", "Senior Software Engineer"))
    _queued(session, b.id, _job(session, "t-2", "Senior Platform Engineer"))
    session.commit()

    assert [j.title for j in services.select_pending_jobs(session)] == [
        "Senior Software Engineer"
    ]
    assert [j["title"] for j in client.get("/api/jobs").json()] == ["Senior Software Engineer"]
    profiles.activate_profile(session, b.id)
    assert [j.title for j in services.select_pending_jobs(session)] == [
        "Senior Platform Engineer"
    ]
    assert [j["title"] for j in client.get("/api/jobs").json()] == ["Senior Platform Engineer"]
    assert client.get("/api/jobs/status-counts").json()["total"] == 1
