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


def _resume(
    session: Session, name: str, *, active: bool = False, profile_id: int | None = None
) -> Resume:
    """A resume owned by ``profile_id`` (default: the session's profile, which
    the flush stamps, creating Default on a fresh DB)."""
    r = Resume(
        original_filename=name,
        pdf_path=f"/tmp/{name}",
        extracted_text="x",
        is_active=active,
        profile_id=profile_id,
    )
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
    mgr = _resume(session, "mgr.pdf", profile_id=b.id)
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
def test_activating_a_profile_without_a_resume_leaves_none_active(session, dangling):
    # A new person has no resume yet: switching to them must not keep the
    # previous person's resume active, and the flag must agree with the profile.
    _resume(session, "ic.pdf", active=True)
    profiles.load_or_create_profile(session)
    session.commit()
    b = SearchProfile(name="B", resume_id=999 if dangling else None)
    session.add(b)
    session.commit()

    profiles.activate_profile(session, b.id)
    assert b.resume_id is None
    assert _active_resume_id(session) is None
    flagged = session.exec(
        select(Resume).where(Resume.is_active == True).execution_options(all_profiles=True)  # noqa: E712
    ).all()
    assert flagged == []


def test_a_dangling_resume_falls_back_to_the_profiles_newest_own(session):
    profiles.load_or_create_profile(session)
    session.commit()
    b = profiles.create_profile(session, name="B")
    own = _resume(session, "b.pdf", profile_id=b.id)
    b.resume_id = 999
    session.add(b)
    session.commit()

    profiles.activate_profile(session, b.id)
    assert b.resume_id == own.id
    assert _active_resume_id(session) == own.id


def test_profiles_only_see_and_use_their_own_resumes(session):
    a_resume = _resume(session, "a.pdf", active=True)
    a = profiles.load_or_create_profile(session)
    session.commit()
    b = profiles.create_profile(session, name="B")
    assert b.resume_id is None  # a blank profile is a new person
    with pytest.raises(LookupError):
        profiles.update_profile_meta(session, b.id, resume_id=a_resume.id)

    _resume(session, "b.pdf", profile_id=b.id)
    session.expire_all()
    assert [r.original_filename for r in session.exec(select(Resume)).all()] == ["a.pdf"]
    profiles.activate_profile(session, b.id)
    session.expire_all()
    assert [r.original_filename for r in session.exec(select(Resume)).all()] == ["b.pdf"]
    assert a.id != b.id


def test_uploaded_resume_becomes_the_active_profiles_resume(session):
    _resume(session, "old.pdf", active=True)
    p = profiles.load_or_create_profile(session)
    session.commit()
    new = _resume(session, "new.pdf")
    profiles.adopt_uploaded_resume(session, new.id)
    session.commit()
    session.refresh(p)
    assert p.resume_id == new.id


def test_clone_copies_criteria_and_its_own_copy_of_the_resume(session):
    r = _resume(session, "r.pdf", active=True)
    src = profiles.load_or_create_profile(session)
    src.required_tech = ["rust"]
    src.home_state = "Missouri"
    session.commit()
    clone = profiles.create_profile(session, name="Copy", clone_from=src.id)
    assert clone.required_tech == ["rust"]
    assert clone.home_state == "Missouri"
    assert clone.is_active is False
    # Its own row (profiles never share one), pointing at the same PDF.
    copy = profiles.owned_resume(session, clone.id, clone.resume_id)
    assert copy is not None and copy.id != r.id
    assert (copy.pdf_path, copy.is_active) == (r.pdf_path, False)


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


def test_deleting_a_profile_removes_its_resumes_but_not_a_shared_pdf(session, tmp_path, monkeypatch):
    from job_applier.config import settings

    monkeypatch.setattr(settings, "resumes_dir", tmp_path)
    shared, own = tmp_path / "shared.pdf", tmp_path / "own.pdf"
    shared.write_bytes(b"%PDF")
    own.write_bytes(b"%PDF")
    a_resume = Resume(original_filename="a.pdf", pdf_path=str(shared), extracted_text="x", is_active=True)
    session.add(a_resume)
    session.commit()
    a = profiles.load_or_create_profile(session)
    session.commit()
    copy = profiles.create_profile(session, name="Copy", clone_from=a.id)  # shares shared.pdf
    _resume(session, "own.pdf", profile_id=copy.id).pdf_path = str(own)
    session.commit()

    profiles.delete_profile(session, copy.id)
    rows = session.exec(select(Resume).execution_options(all_profiles=True)).all()
    assert [r.id for r in rows] == [a_resume.id]
    assert shared.exists()  # still A's
    assert not own.exists()


def test_use_resume_endpoint_switches_only_to_the_profiles_own(client, engine):
    with Session(engine) as s:
        first = _resume(s, "first.pdf", active=True)
        p = profiles.load_or_create_profile(s)
        s.commit()
        second = _resume(s, "second.pdf")
        other = profiles.create_profile(s, name="Other")
        theirs = _resume(s, "theirs.pdf", profile_id=other.id)
        ids = (first.id, second.id, theirs.id, p.id)
    first_id, second_id, theirs_id, _ = ids

    r = client.post(f"/api/resumes/{second_id}/use")
    assert r.status_code == 200 and r.json()["id"] == second_id
    assert [x["original_filename"] for x in client.get("/api/resumes").json() if x["is_active"]] == [
        "second.pdf"
    ]
    assert client.post(f"/api/resumes/{theirs_id}/use").status_code == 404
    assert {x["id"] for x in client.get("/api/resumes").json()} == {first_id, second_id}


def _flagged(session: Session) -> list[int]:
    session.expire_all()
    return [
        r.id
        for r in session.exec(
            select(Resume).where(Resume.is_active == True).execution_options(all_profiles=True)  # noqa: E712
        ).all()
    ]


def test_the_active_flag_always_mirrors_the_profiles_resume(session):
    # resume_id is the source of truth; Resume.is_active only mirrors it. Walk
    # every writer and check the mirror never disagrees with what readers use.
    first = _resume(session, "first.pdf")
    a = profiles.load_or_create_profile(session)
    session.commit()
    assert profiles.active_resume_id(session) == first.id  # newest own, flag unset

    second = _resume(session, "second.pdf")
    profiles.set_active_resume(session, second.id)
    profiles.adopt_uploaded_resume(session, second.id)  # what the upload does
    session.commit()
    assert _flagged(session) == [profiles.active_resume_id(session)] == [second.id]

    profiles.update_profile_meta(session, a.id, resume_id=first.id)
    assert _flagged(session) == [profiles.active_resume_id(session)] == [first.id]

    b = profiles.create_profile(session, name="B", clone_from=a.id)
    profiles.activate_profile(session, b.id)
    assert _flagged(session) == [profiles.active_resume_id(session)] == [b.resume_id]

    blank = profiles.create_profile(session, name="Blank")
    profiles.activate_profile(session, blank.id)
    assert _flagged(session) == [] and profiles.active_resume_id(session) is None

    profiles.activate_profile(session, a.id)
    assert _flagged(session) == [profiles.active_resume_id(session)] == [first.id]


def test_a_stray_flag_never_decides_the_resume(session):
    # Even if the mirror is wrong (a hand edit, an old row), readers follow the profile.
    mine = _resume(session, "mine.pdf")
    p = profiles.load_or_create_profile(session)
    session.commit()
    other = profiles.create_profile(session, name="Other")
    _resume(session, "theirs.pdf", active=True, profile_id=other.id)
    assert p.resume_id == mine.id
    assert profiles.active_resume_id(session) == mine.id
