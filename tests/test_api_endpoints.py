"""Endpoint coverage for the resume-upload, companies, notes, and jobs-filter
routes — the API surface the audit flagged as untested. Also serves as the
regression net for the api/app.py router split.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from job_applier import resume_io, services
from job_applier.api.app import COMPANY_CHECKED_KEY, app
from job_applier.config import settings
from job_applier.models.db import (
    Application,
    ApplicationStatus,
    Company,
    FilterStatus,
    JobPosting,
    Resume,
    get_session,
    set_setting,
)


def _engine():
    e = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(e)
    return e


@pytest.fixture
def client():
    e = _engine()

    def _dep():
        with Session(e) as s:
            yield s

    app.dependency_overrides[get_session] = _dep
    with TestClient(app) as c:
        yield c, e
    app.dependency_overrides.clear()


def _seed_job(session, *, title="Senior Engineer", company="Acme", source_id="t-1",
              ingested_at=None):
    company_row = Company(name=company)
    session.add(company_row)
    session.flush()
    j = JobPosting(
        source="test",
        source_id=source_id,
        url="https://e.com/1",
        title=title,
        description="<p>x</p>",
        dedupe_hash=f"h-{source_id}",
        filter_status=FilterStatus.passed,
        company_id=company_row.id,
    )
    session.add(j)
    session.flush()
    if ingested_at is not None:
        j.ingested_at = ingested_at
        session.add(j)
    session.commit()
    session.refresh(j)
    return j


# ---- resume upload --------------------------------------------------------


def _upload(c, *, name="resume.pdf", content=b"%PDF-1.7 fake bytes"):
    return c.post("/api/resume", files={"file": (name, content, "application/pdf")})


def test_upload_resume_happy_path_sets_active(client, monkeypatch):
    c, e = client
    monkeypatch.setattr(resume_io, "extract_text", lambda b: ("Resume text.", 2))
    monkeypatch.setattr(resume_io, "save_pdf", lambda b, fn: "/tmp/r.pdf")

    r = _upload(c)
    assert r.status_code == 201
    body = r.json()
    assert body["is_active"] is True
    assert body["page_count"] == 2


def test_upload_resume_demotes_previous_active(client, monkeypatch):
    c, e = client
    with Session(e) as s:
        s.add(Resume(original_filename="old.pdf", pdf_path="/tmp/old.pdf",
                     extracted_text="old", is_active=True))
        s.commit()
    monkeypatch.setattr(resume_io, "extract_text", lambda b: ("New text.", 1))
    monkeypatch.setattr(resume_io, "save_pdf", lambda b, fn: "/tmp/new.pdf")

    assert _upload(c, name="new.pdf").status_code == 201
    with Session(e) as s:
        actives = s.exec(select(Resume).where(Resume.is_active == True)).all()  # noqa: E712
    assert len(actives) == 1 and actives[0].original_filename == "new.pdf"


def test_upload_resume_rejects_non_pdf(client):
    c, _ = client
    r = c.post("/api/resume", files={"file": ("resume.txt", b"hi", "text/plain")})
    assert r.status_code == 415


def test_upload_resume_rejects_empty(client):
    c, _ = client
    assert _upload(c, content=b"").status_code == 400


def test_upload_resume_rejects_too_large(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(settings, "max_resume_bytes", 4)
    assert _upload(c, content=b"way too many bytes").status_code == 413


def test_upload_resume_rejects_unreadable_pdf(client, monkeypatch):
    c, _ = client

    def _boom(b):
        raise ValueError("corrupt PDF")

    monkeypatch.setattr(resume_io, "extract_text", _boom)
    assert _upload(c).status_code == 422


def test_upload_resume_rejects_image_only_pdf(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(resume_io, "extract_text", lambda b: ("   ", 1))
    r = _upload(c)
    assert r.status_code == 422
    assert "image-only" in r.json()["detail"]


# ---- companies ------------------------------------------------------------


def test_list_and_block_company_round_trip(client):
    c, e = client
    with Session(e) as s:
        job = _seed_job(s)
        company_id = job.company_id

    listing = c.get("/api/companies")
    assert listing.status_code == 200
    assert any(co["id"] == company_id for co in listing.json())

    blocked = c.post(f"/api/companies/{company_id}/block")
    assert blocked.status_code == 200 and blocked.json()["is_blocked"] is True

    unblocked = c.post(f"/api/companies/{company_id}/block", params={"blocked": False})
    assert unblocked.json()["is_blocked"] is False


def test_block_unknown_company_404(client):
    c, _ = client
    assert c.post("/api/companies/9999/block").status_code == 404


# ---- notes ----------------------------------------------------------------


def test_set_notes_creates_application_row(client):
    c, e = client
    with Session(e) as s:
        job_id = _seed_job(s).id

    r = c.post(f"/api/jobs/{job_id}/notes", json={"notes": "called recruiter"})
    assert r.status_code == 200
    assert r.json()["notes"] == "called recruiter"
    with Session(e) as s:
        app_row = s.exec(select(Application).where(Application.job_id == job_id)).one()
        assert app_row.notes == "called recruiter"
        assert app_row.status == ApplicationStatus.new


def test_set_notes_unknown_job_404(client):
    c, _ = client
    assert c.post("/api/jobs/9999/notes", json={"notes": "x"}).status_code == 404


# ---- jobs list filters ----------------------------------------------------


def test_list_jobs_status_filter(client):
    c, e = client
    with Session(e) as s:
        a = _seed_job(s, title="A Engineer", source_id="a", company="Acme A")
        b = _seed_job(s, title="B Engineer", source_id="b", company="Acme B")
        s.add(Application(job_id=a.id, status=ApplicationStatus.applied))
        s.add(Application(job_id=b.id, status=ApplicationStatus.new))
        s.commit()

    applied = c.get("/api/jobs", params={"status": "applied"}).json()
    assert {j["title"] for j in applied} == {"A Engineer"}


def test_list_jobs_filters_before_pagination(client):
    """The joined-data filters must apply BEFORE limit/offset — an old `applied`
    job must still surface under ?status=applied&limit=2 even when two newer jobs
    would otherwise fill the page."""
    from datetime import datetime, timedelta, timezone

    c, e = client
    now = datetime.now(timezone.utc)
    with Session(e) as s:
        old = _seed_job(s, title="Old Applied", source_id="old", company="Old Co",
                        ingested_at=now - timedelta(days=2))
        for i in range(2):
            _seed_job(s, title=f"New {i}", source_id=f"n{i}", company=f"New {i} Co",
                      ingested_at=now - timedelta(minutes=i))
        s.add(Application(job_id=old.id, status=ApplicationStatus.applied))
        s.commit()

    result = c.get("/api/jobs", params={"status": "applied", "limit": 2}).json()
    assert {j["title"] for j in result} == {"Old Applied"}


def test_list_jobs_unscored_only_filter(client):
    c, e = client
    with Session(e) as s:
        _seed_job(s, title="Unscored Engineer", source_id="u")

    unscored = c.get("/api/jobs", params={"unscored_only": True}).json()
    assert any(j["title"] == "Unscored Engineer" for j in unscored)


# ---- posting search (Ctrl/Cmd-K palette) ----------------------------------


def test_search_matches_title_and_company(client):
    c, e = client
    with Session(e) as s:
        _seed_job(s, title="Staff Platform Engineer", source_id="s1", company="Acme")
        _seed_job(s, title="Designer", source_id="s2", company="Globex")

    by_title = c.get("/api/search", params={"q": "platform"}).json()
    assert [j["title"] for j in by_title] == ["Staff Platform Engineer"]

    # Company match is case-insensitive, and returns that company's postings.
    by_company = c.get("/api/search", params={"q": "globex"}).json()
    assert [j["company"]["name"] for j in by_company] == ["Globex"]


def test_search_spans_archived_and_manual_postings(client):
    """Wider than the queue on purpose: the point is to find a job you know was
    ingested, even if it was archived or routed to manual review."""
    c, e = client
    with Session(e) as s:
        archived = _seed_job(s, title="Archived Engineer", source_id="s1")
        manual = _seed_job(s, title="Manual Engineer", source_id="s2", company="Manual Co")
        manual.filter_status = FilterStatus.manual
        s.add(manual)
        s.add(Application(job_id=archived.id, status=ApplicationStatus.archived))
        s.commit()

    found = {j["title"] for j in c.get("/api/search", params={"q": "engineer"}).json()}
    assert found == {"Archived Engineer", "Manual Engineer"}


def test_search_hides_duplicates_and_short_queries(client):
    c, e = client
    with Session(e) as s:
        canonical = _seed_job(s, title="Backend Engineer", source_id="s1")
        dupe = _seed_job(s, title="Backend Engineer", source_id="s2", company="Acme Dupe")
        dupe.duplicate_of = canonical.id
        s.add(dupe)
        s.commit()

    assert len(c.get("/api/search", params={"q": "backend"}).json()) == 1
    # Below the minimum term length the endpoint returns nothing rather than
    # dumping the table into the palette.
    assert c.get("/api/search", params={"q": "b"}).json() == []


def test_search_treats_wildcards_literally(client):
    c, e = client
    with Session(e) as s:
        _seed_job(s, title="Engineer, 50% travel", source_id="s1")
        _seed_job(s, title="Designer", source_id="s2", company="Globex")

    assert [j["title"] for j in c.get("/api/search", params={"q": "50%"}).json()] == [
        "Engineer, 50% travel"
    ]


def test_search_ranks_exact_and_prefix_matches_first(client):
    c, e = client
    with Session(e) as s:
        _seed_job(s, title="Senior Data Engineer", source_id="s1", company="Acme")
        _seed_job(s, title="Data", source_id="s2", company="Globex")
        _seed_job(s, title="Data Scientist", source_id="s3", company="Initech")

    titles = [j["title"] for j in c.get("/api/search", params={"q": "data"}).json()]
    assert titles == ["Data", "Data Scientist", "Senior Data Engineer"]


# ---- SQLite busy-lock -> 503 handler --------------------------------------


@pytest.fixture
def quiet_client():
    """Like `client`, but returns 500 responses instead of re-raising server
    errors — so the handler's re-raise branch is observable as a response."""
    e = _engine()

    def _dep():
        with Session(e) as s:
            yield s

    app.dependency_overrides[get_session] = _dep
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    app.dependency_overrides.clear()


def _raise_operational(message: str):
    def _boom(*_a, **_k):
        raise OperationalError(
            "SELECT jobposting.id FROM jobposting", {}, sqlite3.OperationalError(message)
        )

    return _boom


@pytest.mark.parametrize(
    "message",
    [
        # SQLITE_BUSY: another connection holds the write lock.
        "database is locked",
        # SQLITE_LOCKED: a shared-cache table lock. Different wording, same
        # "come back in a moment" meaning — it used to fall through to a 500.
        "database table is locked",
    ],
)
def test_busy_database_returns_retryable_503(quiet_client, monkeypatch, message):
    monkeypatch.setattr(services, "search_jobs", _raise_operational(message))

    r = quiet_client.get("/api/search", params={"q": "engineer"})
    assert r.status_code == 503
    assert r.headers["Retry-After"] == "2"
    assert "try again" in r.json()["detail"].lower()


def test_non_lock_database_error_is_not_masked_as_503(quiet_client, monkeypatch):
    """A schema/query bug must stay a 500. Reporting it as "the database is busy,
    try again" would send the user round a retry loop that can never succeed."""
    monkeypatch.setattr(services, "search_jobs", _raise_operational("no such table: jobposting"))

    r = quiet_client.get("/api/search", params={"q": "engineer"})
    assert r.status_code == 500
    assert "Retry-After" not in r.headers


# ---- company coverage (a /search page-load dependency) ---------------------


@pytest.mark.parametrize(
    "stored,expected",
    [
        ("2026-08-01T12:00:00+00:00", "2026-08-01T12:00:00Z"),
        # Garbage in the free-form setting (hand-edited DB, a half-written row, a
        # future format change) degrades to "never checked" instead of 500ing the
        # endpoint — /search fetches it on load, so a failure here would lock the
        # user out of the page that resets the company list.
        ("last tuesday", None),
        ("", None),
    ],
)
def test_company_coverage_tolerates_unparseable_checked_at(client, stored, expected):
    c, e = client
    with Session(e) as s:
        set_setting(s, COMPANY_CHECKED_KEY, stored)

    r = c.get("/api/company-coverage")
    assert r.status_code == 200, r.text
    last = r.json()["last_checked_at"]
    if expected is None:
        assert last is None
    else:
        assert datetime.fromisoformat(last.replace("Z", "+00:00")) == datetime.fromisoformat(
            expected.replace("Z", "+00:00")
        )
