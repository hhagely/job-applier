"""Endpoint coverage for the resume-upload, companies, notes, and jobs-filter
routes — the API surface the audit flagged as untested. Also serves as the
regression net for the api/app.py router split.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from job_applier import resume_io
from job_applier.api.app import app
from job_applier.config import settings
from job_applier.models.db import (
    Application,
    ApplicationStatus,
    Company,
    FilterStatus,
    JobPosting,
    Resume,
    get_session,
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
