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


def _seed_job(engine, *, source_id: str = "t-1", title: str = "Senior Engineer") -> int:
    with Session(engine) as s:
        job = JobPosting(
            source="test",
            source_id=source_id,
            url=f"https://example.com/jobs/{source_id}",
            title=title,
            description="TypeScript role.",
            dedupe_hash=f"h-{source_id}",
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


def _deactivate(engine, resume_id: int) -> None:
    with Session(engine) as s:
        r = s.get(Resume, resume_id)
        r.is_active = False
        s.add(r)
        s.commit()


def test_score_is_not_stale_when_resume_matches_active(client):
    c, engine = client
    job_id = _seed_job(engine)
    _seed_resume(engine, filename="v1.pdf", active=True)
    c.post(f"/api/jobs/{job_id}/score", json={"score": 70, "rubric": {}})

    body = c.get(f"/api/jobs/{job_id}").json()
    assert body["score"]["is_stale"] is False


def test_score_becomes_stale_after_uploading_new_resume(client):
    c, engine = client
    job_id = _seed_job(engine)
    r1 = _seed_resume(engine, filename="v1.pdf", active=True)
    c.post(f"/api/jobs/{job_id}/score", json={"score": 70, "rubric": {}})

    _deactivate(engine, r1)
    _seed_resume(engine, filename="v2.pdf", active=True)

    body = c.get(f"/api/jobs/{job_id}").json()
    assert body["score"]["is_stale"] is True
    # And it also shows up via the list endpoint.
    listed = c.get("/api/jobs").json()
    assert listed[0]["score"]["is_stale"] is True


def test_tailored_score_is_never_stale(client):
    c, engine = client
    job_id = _seed_job(engine)
    r1 = _seed_resume(engine, filename="v1.pdf", active=True)
    c.post(
        f"/api/jobs/{job_id}/score",
        json={"score": 80, "rubric": {}, "score_kind": "tailored"},
    )

    _deactivate(engine, r1)
    _seed_resume(engine, filename="v2.pdf", active=True)

    body = c.get(f"/api/jobs/{job_id}").json()
    assert body["score"]["score_kind"] == "tailored"
    assert body["score"]["is_stale"] is False


def test_pending_match_excludes_stale_by_default(client):
    c, engine = client
    job_id = _seed_job(engine)
    r1 = _seed_resume(engine, filename="v1.pdf", active=True)
    c.post(f"/api/jobs/{job_id}/score", json={"score": 70, "rubric": {}})

    _deactivate(engine, r1)
    _seed_resume(engine, filename="v2.pdf", active=True)

    rows = c.get("/api/pending-match").json()
    assert all(j["id"] != job_id for j in rows)


def test_pending_match_includes_stale_when_requested(client):
    c, engine = client
    scored_job = _seed_job(engine, source_id="s-1", title="Scored role")
    unscored_job = _seed_job(engine, source_id="s-2", title="Unscored role")
    r1 = _seed_resume(engine, filename="v1.pdf", active=True)
    c.post(f"/api/jobs/{scored_job}/score", json={"score": 70, "rubric": {}})

    _deactivate(engine, r1)
    _seed_resume(engine, filename="v2.pdf", active=True)

    rows = c.get("/api/pending-match?include_stale=true").json()
    ids = {j["id"] for j in rows}
    assert scored_job in ids
    assert unscored_job in ids


def test_pending_match_include_stale_skips_tailored_scores(client):
    c, engine = client
    job_id = _seed_job(engine)
    r1 = _seed_resume(engine, filename="v1.pdf", active=True)
    c.post(
        f"/api/jobs/{job_id}/score",
        json={"score": 80, "rubric": {}, "score_kind": "tailored"},
    )

    _deactivate(engine, r1)
    _seed_resume(engine, filename="v2.pdf", active=True)

    rows = c.get("/api/pending-match?include_stale=true").json()
    assert all(j["id"] != job_id for j in rows)


def test_stale_count_endpoint(client):
    c, engine = client
    stale_a = _seed_job(engine, source_id="a")
    stale_b = _seed_job(engine, source_id="b")
    fresh = _seed_job(engine, source_id="c")
    r1 = _seed_resume(engine, filename="v1.pdf", active=True)
    c.post(f"/api/jobs/{stale_a}/score", json={"score": 60, "rubric": {}})
    c.post(f"/api/jobs/{stale_b}/score", json={"score": 65, "rubric": {}})

    _deactivate(engine, r1)
    r2 = _seed_resume(engine, filename="v2.pdf", active=True)
    c.post(f"/api/jobs/{fresh}/score", json={"score": 70, "rubric": {}})

    body = c.get("/api/scores/stale-count").json()
    assert body == {"count": 2}

    # Re-scoring against the active resume drops the count.
    c.post(f"/api/jobs/{stale_a}/score", json={"score": 75, "rubric": {}})
    body = c.get("/api/scores/stale-count").json()
    assert body == {"count": 1}
    # Sanity: the rescored row points at r2 now.
    assert c.get(f"/api/jobs/{stale_a}").json()["score"]["resume_id"] == r2


def test_stale_count_zero_when_no_active_resume(client):
    c, _ = client
    body = c.get("/api/scores/stale-count").json()
    assert body == {"count": 0}
