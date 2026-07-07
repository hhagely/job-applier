from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from job_applier.api.app import app
from job_applier.models import JobPosting
from job_applier.models.db import FilterStatus, get_session


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


def _seed_job(engine, *, source_id: str = "u-1") -> int:
    with Session(engine) as s:
        job = JobPosting(
            source="test",
            source_id=source_id,
            url=f"https://example.com/jobs/{source_id}",
            title="Senior Engineer",
            description="role",
            dedupe_hash=source_id,
            filter_status=FilterStatus.passed,
        )
        s.add(job)
        s.commit()
        s.refresh(job)
        return job.id


def test_mark_used_creates_application_row_and_stamps_time(client):
    c, engine = client
    job_id = _seed_job(engine)

    res = c.post(f"/api/jobs/{job_id}/unemployment", json={"used": True})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["used_for_unemployment"] is True
    assert body["used_for_unemployment_at"] is not None
    # No prior status change — defaults to "new".
    assert body["status"] == "new"


def test_unmark_clears_flag_and_timestamp(client):
    c, engine = client
    job_id = _seed_job(engine)

    c.post(f"/api/jobs/{job_id}/unemployment", json={"used": True})
    res = c.post(f"/api/jobs/{job_id}/unemployment", json={"used": False})
    assert res.status_code == 200
    body = res.json()
    assert body["used_for_unemployment"] is False
    assert body["used_for_unemployment_at"] is None


def test_flag_surfaces_on_job_listing(client):
    c, engine = client
    job_id = _seed_job(engine)
    c.post(f"/api/jobs/{job_id}/unemployment", json={"used": True})

    listed = c.get("/api/jobs").json()
    row = next(j for j in listed if j["id"] == job_id)
    assert row["application"]["used_for_unemployment"] is True


def test_mark_preserves_existing_status(client):
    c, engine = client
    job_id = _seed_job(engine)
    c.patch(f"/api/jobs/{job_id}/status", json={"status": "applied"})

    res = c.post(f"/api/jobs/{job_id}/unemployment", json={"used": True})
    assert res.status_code == 200
    assert res.json()["status"] == "applied"


def test_unmark_unknown_job_returns_404(client):
    c, _ = client
    res = c.post("/api/jobs/9999/unemployment", json={"used": True})
    assert res.status_code == 404


def test_bulk_mark_flags_all_selected(client):
    c, engine = client
    j1 = _seed_job(engine, source_id="u-b1")
    j2 = _seed_job(engine, source_id="u-b2")

    res = c.post(
        "/api/jobs/bulk-unemployment", json={"job_ids": [j1, j2], "used": True}
    )
    assert res.status_code == 200, res.text
    rows = res.json()
    assert len(rows) == 2
    assert all(r["used_for_unemployment"] is True for r in rows)
    assert all(r["used_for_unemployment_at"] is not None for r in rows)


def test_bulk_unmark_clears_selected(client):
    c, engine = client
    j1 = _seed_job(engine, source_id="u-b3")
    j2 = _seed_job(engine, source_id="u-b4")
    c.post("/api/jobs/bulk-unemployment", json={"job_ids": [j1, j2], "used": True})

    res = c.post(
        "/api/jobs/bulk-unemployment", json={"job_ids": [j1, j2], "used": False}
    )
    assert res.status_code == 200
    rows = res.json()
    assert all(r["used_for_unemployment"] is False for r in rows)
    assert all(r["used_for_unemployment_at"] is None for r in rows)


def test_bulk_empty_ids_returns_422(client):
    c, _ = client
    res = c.post("/api/jobs/bulk-unemployment", json={"job_ids": [], "used": True})
    assert res.status_code == 422


def test_bulk_unknown_job_returns_404(client):
    c, engine = client
    j1 = _seed_job(engine, source_id="u-b5")
    res = c.post(
        "/api/jobs/bulk-unemployment", json={"job_ids": [j1, 9999], "used": True}
    )
    assert res.status_code == 404


def test_migration_adds_unemployment_columns_on_existing_db(tmp_path, monkeypatch):
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
    assert {"used_for_unemployment", "used_for_unemployment_at"}.issubset(cols)
