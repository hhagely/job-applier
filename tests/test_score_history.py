from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from job_applier.api.app import app
from job_applier.models import JobPosting, Resume
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


def _seed_job(engine) -> int:
    with Session(engine) as s:
        job = JobPosting(
            source="test",
            source_id="t-1",
            url="https://example.com/jobs/1",
            title="Senior Engineer",
            description="TypeScript role.",
            dedupe_hash="h1",
            filter_status=FilterStatus.passed,
        )
        s.add(job)
        s.commit()
        s.refresh(job)
        return job.id


def _seed_resume(engine, *, filename: str, active: bool) -> int:
    with Session(engine) as s:
        r = Resume(
            original_filename=filename,
            pdf_path=f"/tmp/{filename}",
            extracted_text="resume text",
            is_active=active,
        )
        s.add(r)
        s.commit()
        s.refresh(r)
        return r.id


def test_rescoring_snapshots_previous_score_with_resume(client):
    c, engine = client
    job_id = _seed_job(engine)
    r1 = _seed_resume(engine, filename="v1.pdf", active=True)

    first = c.post(
        f"/api/jobs/{job_id}/score",
        json={"score": 60, "rubric": {"jd": 30}, "reasoning": "ok"},
    )
    assert first.status_code == 200, first.text
    assert first.json()["resume_id"] == r1
    assert first.json()["resume_filename"] == "v1.pdf"

    # Swap active resume; re-score with new payload.
    with Session(engine) as s:
        old = s.get(Resume, r1)
        old.is_active = False
        s.add(old)
        s.commit()
    r2 = _seed_resume(engine, filename="v2.pdf", active=True)

    second = c.post(
        f"/api/jobs/{job_id}/score",
        json={"score": 85, "rubric": {"jd": 45}, "reasoning": "better fit"},
    )
    assert second.status_code == 200
    assert second.json()["score"] == 85
    assert second.json()["resume_id"] == r2
    assert second.json()["resume_filename"] == "v2.pdf"

    history = c.get(f"/api/jobs/{job_id}/score-history")
    assert history.status_code == 200
    rows = history.json()
    assert len(rows) == 1
    assert rows[0]["score"] == 60
    assert rows[0]["reasoning"] == "ok"
    assert rows[0]["resume_id"] == r1
    assert rows[0]["resume_filename"] == "v1.pdf"


def test_score_history_empty_for_first_score(client):
    c, engine = client
    job_id = _seed_job(engine)
    _seed_resume(engine, filename="v1.pdf", active=True)

    c.post(f"/api/jobs/{job_id}/score", json={"score": 75, "rubric": {}})
    history = c.get(f"/api/jobs/{job_id}/score-history")
    assert history.status_code == 200
    assert history.json() == []


def test_score_history_missing_job_returns_404(client):
    c, _ = client
    assert c.get("/api/jobs/9999/score-history").status_code == 404


def test_score_without_active_resume_records_none(client):
    c, engine = client
    job_id = _seed_job(engine)

    res = c.post(f"/api/jobs/{job_id}/score", json={"score": 50, "rubric": {}})
    assert res.status_code == 200
    assert res.json()["resume_id"] is None
    assert res.json()["resume_filename"] is None


def test_default_score_kind_is_baseline(client):
    c, engine = client
    job_id = _seed_job(engine)
    _seed_resume(engine, filename="v1.pdf", active=True)

    res = c.post(f"/api/jobs/{job_id}/score", json={"score": 70, "rubric": {}})
    assert res.status_code == 200
    assert res.json()["score_kind"] == "baseline"


def test_tailored_score_records_kind_and_null_resume(client):
    c, engine = client
    job_id = _seed_job(engine)
    _seed_resume(engine, filename="v1.pdf", active=True)

    res = c.post(
        f"/api/jobs/{job_id}/score",
        json={"score": 80, "rubric": {}, "score_kind": "tailored"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["score_kind"] == "tailored"
    assert body["resume_id"] is None
    assert body["resume_filename"] is None


def test_history_preserves_prior_score_kind(client):
    c, engine = client
    job_id = _seed_job(engine)
    _seed_resume(engine, filename="v1.pdf", active=True)

    c.post(f"/api/jobs/{job_id}/score", json={"score": 60, "rubric": {}})
    c.post(
        f"/api/jobs/{job_id}/score",
        json={"score": 85, "rubric": {}, "score_kind": "tailored"},
    )

    history = c.get(f"/api/jobs/{job_id}/score-history").json()
    assert len(history) == 1
    assert history[0]["score_kind"] == "baseline"
    assert history[0]["score"] == 60


def test_rejects_unknown_score_kind(client):
    c, engine = client
    job_id = _seed_job(engine)
    res = c.post(
        f"/api/jobs/{job_id}/score",
        json={"score": 50, "rubric": {}, "score_kind": "bogus"},
    )
    assert res.status_code == 422
