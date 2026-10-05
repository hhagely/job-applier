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
from job_applier.filters import build_config
from job_applier.ingest import IngestStats, ingest_one
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
# Ingest linking
# ---------------------------------------------------------------------------


_TS = build_config(role_titles=[], seniority_terms=["senior"], required_tech=["typescript"], excluded_tech=[])
_RUST = build_config(role_titles=[], seniority_terms=["senior"], required_tech=["rust"], excluded_tech=[])


def _links(session: Session) -> set[tuple[int, int]]:
    return {
        (link.job_id, link.search_profile_id)
        for link in session.exec(select(JobProfileLink)).all()
    }


def test_duplicate_passing_the_new_profile_is_linked_not_reinserted(session):
    a = profiles.load_or_create_profile(session)
    b = profiles.create_profile(session, name="B")
    ingest_one(session, _raw(), IngestStats(), filter_config=_TS, profile_id=a.id)
    session.commit()
    job_id = session.exec(select(JobPosting.id)).one()

    stats = IngestStats()
    ingest_one(session, _raw(), stats, filter_config=_TS, profile_id=b.id)
    session.commit()

    assert stats.linked_existing == 1
    assert stats.inserted == 0
    assert len(session.exec(select(JobPosting)).all()) == 1
    assert _links(session) == {(job_id, a.id), (job_id, b.id)}


def test_duplicate_failing_the_new_profile_is_not_linked(session):
    a = profiles.load_or_create_profile(session)
    b = profiles.create_profile(session, name="B")
    ingest_one(session, _raw(), IngestStats(), filter_config=_TS, profile_id=a.id)
    session.commit()

    stats = IngestStats()
    ingest_one(session, _raw(), stats, filter_config=_RUST, profile_id=b.id)
    session.commit()

    assert stats.linked_existing == 0
    assert stats.skipped_duplicate == 1
    assert {pid for _, pid in _links(session)} == {a.id}


def test_cross_source_duplicate_is_linked_to_the_new_profile(session):
    a = profiles.load_or_create_profile(session)
    b = profiles.create_profile(session, name="B")
    ingest_one(session, _raw(), IngestStats(), filter_config=_TS, profile_id=a.id)
    session.commit()

    # Same company + title from a different source: the cross-source path.
    stats = IngestStats()
    ingest_one(
        session,
        _raw(source="other", source_id="o-9", url="https://other.example/9"),
        stats,
        filter_config=_TS,
        profile_id=b.id,
    )
    session.commit()

    assert stats.linked_existing == 1
    assert len(session.exec(select(JobPosting)).all()) == 1


# ---------------------------------------------------------------------------
# Scoping: pending-match + queue
# ---------------------------------------------------------------------------


def test_pending_match_and_queue_are_scoped_to_the_active_profile(session, client):
    a = profiles.load_or_create_profile(session)
    session.commit()
    b = profiles.create_profile(session, name="B")
    ingest_one(session, _raw(), IngestStats(), filter_config=_TS, profile_id=a.id)
    ingest_one(
        session,
        _raw(source_id="t-2", title="Senior Platform Engineer", url="https://example.com/2"),
        IngestStats(),
        filter_config=_TS,
        profile_id=b.id,
    )
    session.commit()

    assert [j.title for j in services.select_pending_jobs(session)] == [
        "Senior Software Engineer"
    ]
    profiles.activate_profile(session, b.id)
    assert [j.title for j in services.select_pending_jobs(session)] == [
        "Senior Platform Engineer"
    ]

    scoped = client.get(f"/api/jobs?profile_id={a.id}").json()
    assert [j["title"] for j in scoped] == ["Senior Software Engineer"]
    assert len(client.get("/api/jobs").json()) == 2
    counts = client.get(f"/api/jobs/status-counts?profile_id={b.id}").json()
    assert counts["total"] == 1
