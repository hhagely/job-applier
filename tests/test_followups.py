from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from job_applier.api.app import app
from job_applier.models import Application, JobPosting
from job_applier.models.db import ApplicationStatus, FilterStatus, get_session


@pytest.fixture
def client():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    def _session_dep():
        with Session(engine) as s:
            yield s

    app.dependency_overrides[get_session] = _session_dep
    with TestClient(app) as c:
        yield c, engine
    app.dependency_overrides.clear()


def _seed_job(engine, *, source_id: str = "t-1", title: str = "Senior Engineer") -> int:
    with Session(engine) as s:
        job = JobPosting(
            source="test",
            source_id=source_id,
            url=f"https://example.com/jobs/{source_id}",
            title=title,
            description="role",
            dedupe_hash=source_id,
            filter_status=FilterStatus.passed,
        )
        s.add(job)
        s.commit()
        s.refresh(job)
        return job.id


def test_applied_status_defaults_next_followup_to_applied_at_plus_7d(client):
    c, engine = client
    job_id = _seed_job(engine)

    res = c.patch(f"/api/jobs/{job_id}/status", json={"status": "applied"})
    assert res.status_code == 200, res.text
    body = res.json()
    applied_at = _parse_dt(body["applied_at"])
    next_followup = _parse_dt(body["next_followup_at"])
    assert next_followup - applied_at == timedelta(days=7)
    assert body["outcome"] is None


def _parse_dt(s: str) -> datetime:
    """Parse an ISO timestamp returned by the API; SQLite drops tz info."""
    dt = datetime.fromisoformat(s)
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def test_explicit_followup_date_overrides_default(client):
    c, engine = client
    job_id = _seed_job(engine)
    custom = datetime(2030, 1, 1, tzinfo=timezone.utc)

    res = c.patch(
        f"/api/jobs/{job_id}/status",
        json={"status": "applied", "next_followup_at": custom.isoformat()},
    )
    assert res.status_code == 200
    assert _parse_dt(res.json()["next_followup_at"]) == custom


def test_non_applied_status_does_not_set_followup(client):
    c, engine = client
    job_id = _seed_job(engine)

    res = c.patch(f"/api/jobs/{job_id}/status", json={"status": "interested"})
    assert res.status_code == 200
    assert res.json()["next_followup_at"] is None


def test_followups_endpoint_returns_overdue_only(client):
    c, engine = client
    overdue_id = _seed_job(engine, source_id="t-overdue", title="Overdue role")
    future_id = _seed_job(engine, source_id="t-future", title="Future role")
    other_id = _seed_job(engine, source_id="t-other", title="Not applied")

    past = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    future = (datetime.now(timezone.utc) + timedelta(days=14)).isoformat()
    c.patch(
        f"/api/jobs/{overdue_id}/status",
        json={"status": "applied", "next_followup_at": past},
    )
    c.patch(
        f"/api/jobs/{future_id}/status",
        json={"status": "applied", "next_followup_at": future},
    )
    c.patch(f"/api/jobs/{other_id}/status", json={"status": "interested"})

    res = c.get("/api/followups")
    assert res.status_code == 200
    ids = [j["id"] for j in res.json()]
    assert ids == [overdue_id]


def test_followups_includes_screening_and_interviewing(client):
    c, engine = client
    applied_id = _seed_job(engine, source_id="t-applied", title="Applied")
    screening_id = _seed_job(engine, source_id="t-screen", title="Screening")
    interview_id = _seed_job(engine, source_id="t-iview", title="Interviewing")
    rejected_id = _seed_job(engine, source_id="t-rej", title="Rejected")

    past = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    for jid in (applied_id, screening_id, interview_id, rejected_id):
        c.patch(
            f"/api/jobs/{jid}/status",
            json={"status": "applied", "next_followup_at": past},
        )
    # Move two forward in the pipeline (keeping next_followup_at intact)
    c.patch(f"/api/jobs/{screening_id}/status", json={"status": "screening"})
    c.patch(f"/api/jobs/{interview_id}/status", json={"status": "interviewing"})
    c.patch(f"/api/jobs/{rejected_id}/status", json={"status": "rejected"})

    ids = {j["id"] for j in c.get("/api/followups").json()}
    assert ids == {applied_id, screening_id, interview_id}


def test_followups_excludes_rows_with_outcome(client):
    c, engine = client
    job_id = _seed_job(engine)
    past = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    c.patch(
        f"/api/jobs/{job_id}/status",
        json={"status": "applied", "next_followup_at": past},
    )
    assert [j["id"] for j in c.get("/api/followups").json()] == [job_id]

    res = c.post(f"/api/jobs/{job_id}/followup", json={"outcome": "ghosted"})
    assert res.status_code == 200
    assert res.json()["outcome"] == "ghosted"
    assert c.get("/api/followups").json() == []


def test_followup_endpoint_partial_updates(client):
    c, engine = client
    job_id = _seed_job(engine)
    past = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    c.patch(
        f"/api/jobs/{job_id}/status",
        json={"status": "applied", "next_followup_at": past},
    )

    new_followup = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
    contact = datetime.now(timezone.utc).isoformat()
    res = c.post(
        f"/api/jobs/{job_id}/followup",
        json={"next_followup_at": new_followup, "last_contact_at": contact},
    )
    assert res.status_code == 200
    body = res.json()
    assert _parse_dt(body["next_followup_at"]) == _parse_dt(new_followup)
    assert _parse_dt(body["last_contact_at"]) == _parse_dt(contact)
    assert body["outcome"] is None


def test_followup_endpoint_without_application_row_returns_409(client):
    c, engine = client
    job_id = _seed_job(engine)
    res = c.post(f"/api/jobs/{job_id}/followup", json={"outcome": "ghosted"})
    assert res.status_code == 409


def test_followups_sorted_most_overdue_first(client):
    c, engine = client
    a = _seed_job(engine, source_id="t-a", title="A")
    b = _seed_job(engine, source_id="t-b", title="B")

    older = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    newer = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    c.patch(f"/api/jobs/{a}/status", json={"status": "applied", "next_followup_at": newer})
    c.patch(f"/api/jobs/{b}/status", json={"status": "applied", "next_followup_at": older})

    ids = [j["id"] for j in c.get("/api/followups").json()]
    assert ids == [b, a]


def test_bulk_status_applies_followup_default(client):
    c, engine = client
    j1 = _seed_job(engine, source_id="b1", title="B1")
    j2 = _seed_job(engine, source_id="b2", title="B2")
    res = c.post("/api/jobs/bulk-status", json={"job_ids": [j1, j2], "status": "applied"})
    assert res.status_code == 200
    for row in res.json():
        assert row["next_followup_at"] is not None
        delta = _parse_dt(row["next_followup_at"]) - _parse_dt(
            row["applied_at"]
        )
        assert delta == timedelta(days=7)


def test_existing_applied_row_status_change_preserves_followup(client):
    """Re-applying ``applied`` shouldn't clobber a manually-set follow-up date."""
    c, engine = client
    job_id = _seed_job(engine)
    custom = datetime(2030, 6, 1, tzinfo=timezone.utc)
    c.patch(
        f"/api/jobs/{job_id}/status",
        json={"status": "applied", "next_followup_at": custom.isoformat()},
    )
    # Toggle to interested then back to applied with no date — preserved row
    # already has applied_at + followup, so default should NOT overwrite.
    c.patch(f"/api/jobs/{job_id}/status", json={"status": "interested"})
    res = c.patch(f"/api/jobs/{job_id}/status", json={"status": "applied"})
    assert _parse_dt(res.json()["next_followup_at"]) == custom


def test_migration_adds_columns_on_existing_db(tmp_path, monkeypatch):
    """Boot against a pre-existing DB without the new columns; migration should add them."""
    import sqlite3

    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE application (
            id INTEGER PRIMARY KEY,
            job_id INTEGER NOT NULL UNIQUE,
            status VARCHAR NOT NULL,
            notes VARCHAR,
            applied_at DATETIME,
            updated_at DATETIME NOT NULL
        );
        """
    )
    conn.commit()
    conn.close()

    from job_applier import models
    from job_applier.config import settings

    monkeypatch.setattr(settings, "db_path", db_path)
    monkeypatch.setattr(models.db, "_engine", None)

    models.db.create_db_and_tables()

    conn = sqlite3.connect(db_path)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(application)")}
    conn.close()
    assert {"next_followup_at", "last_contact_at", "outcome"}.issubset(cols)
