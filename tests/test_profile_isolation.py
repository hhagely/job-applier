"""Two profiles (two people) on one install never see each other's state.

Status, notes, scores, the blacklist, preferences, and drafts are per profile.
The postings themselves are shared. Each test acts as profile A, switches to B
the way the UI does, and checks B starts clean and A's data survives.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from job_applier import pdf
from job_applier.ai import tasks
from job_applier.api.app import app
from job_applier.config import settings
from job_applier.models import Application, JobPosting, MatchScore
from job_applier.models.db import (
    FilterStatus,
    current_profile_id,
    get_session,
    session_profile_id,
)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "applications_dir", tmp_path / "applications")
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)

    def _session_dep():
        with Session(engine) as s:
            yield s

    app.dependency_overrides[get_session] = _session_dep
    with TestClient(app) as c:
        a = c.get("/api/search-profile").json()
        a_id = c.put("/api/search-profile", json={}).json()["id"]  # creates Default
        b_id = c.post("/api/search-profiles", json={"name": "Partner"}).json()["id"]
        with Session(engine) as s:
            job = JobPosting(
                source="test",
                source_id="j-1",
                url="https://example.com/1",
                title="Senior Engineer",
                description="TypeScript.",
                dedupe_hash="h-1",
                filter_status=FilterStatus.passed,
            )
            s.add(job)
            s.commit()
            job_id = job.id
        assert a["using_defaults"] is True
        yield c, engine, a_id, b_id, job_id
    app.dependency_overrides.clear()


def _switch(c, profile_id):
    assert c.post(f"/api/search-profiles/{profile_id}/activate").status_code == 200


def test_application_status_and_notes_are_per_profile(setup):
    c, engine, a, b, job = setup
    c.patch(f"/api/jobs/{job}/status", json={"status": "applied"})
    c.post(f"/api/jobs/{job}/notes", json={"notes": "A's notes"})

    _switch(c, b)
    assert c.get(f"/api/jobs/{job}").json()["application"] is None
    c.patch(f"/api/jobs/{job}/status", json={"status": "interested"})

    _switch(c, a)
    app_a = c.get(f"/api/jobs/{job}").json()["application"]
    assert app_a["status"] == "applied"
    assert app_a["notes"] == "A's notes"

    with Session(engine) as s:
        rows = s.exec(select(Application).execution_options(all_profiles=True)).all()
    assert sorted((r.profile_id, r.status.value) for r in rows) == [
        (a, "applied"),
        (b, "interested"),
    ]


def test_scores_are_per_profile(setup):
    c, engine, a, b, job = setup
    c.post(f"/api/jobs/{job}/score", json={"score": 91, "rubric": {}})
    _switch(c, b)
    assert c.get(f"/api/jobs/{job}").json()["score"] is None
    c.post(f"/api/jobs/{job}/score", json={"score": 40, "rubric": {}})
    assert c.get(f"/api/jobs/{job}").json()["score"]["score"] == 40

    _switch(c, a)
    assert c.get(f"/api/jobs/{job}").json()["score"]["score"] == 91
    # B's score went into its own row, not over A's (no history snapshot of 91).
    assert c.get(f"/api/jobs/{job}/score-history").json() == []
    with Session(engine) as s:
        n = len(s.exec(select(MatchScore).execution_options(all_profiles=True)).all())
    assert n == 2


def test_status_counts_only_count_the_active_profile(setup):
    c, _, a, b, job = setup
    c.patch(f"/api/jobs/{job}/status", json={"status": "applied"})
    assert c.get("/api/jobs/status-counts").json()["counts"]["applied"] == 1
    _switch(c, b)
    counts = c.get("/api/jobs/status-counts").json()["counts"]
    assert counts["applied"] == 0
    assert counts["none"] == 1


def test_blacklist_is_per_profile(setup):
    c, _, a, b, _job = setup
    assert c.post("/api/blacklist", json={"name": "Meta"}).status_code in (200, 201)
    assert [x["name"] for x in c.get("/api/blacklist").json()] == ["Meta"]
    _switch(c, b)
    assert c.get("/api/blacklist").json() == []
    # The same company can be blacklisted independently by the other person.
    assert c.post("/api/blacklist", json={"name": "Meta, Inc."}).status_code in (200, 201)
    _switch(c, a)
    assert [x["name"] for x in c.get("/api/blacklist").json()] == ["Meta"]


def test_preferences_are_per_profile(setup):
    c, _, a, b, _job = setup
    assert c.patch("/api/preferences", json={"ghosted_after_days": 20}).status_code == 200
    _switch(c, b)
    assert c.get("/api/preferences").json()["ghosted_after_days"] == 45
    c.patch("/api/preferences", json={"ghosted_after_days": 90})
    _switch(c, a)
    assert c.get("/api/preferences").json()["ghosted_after_days"] == 20


def test_drafts_are_per_profile(setup, monkeypatch):
    c, _, a, b, job = setup
    monkeypatch.setattr(pdf, "render_to_pdf", lambda _url: b"%PDF fake")
    c.post(f"/api/jobs/{job}/draft", json={"resume_md": "# A resume\n"})
    _switch(c, b)
    draft_b = c.get(f"/api/jobs/{job}/draft?include_markdown=true").json()
    assert draft_b["has_resume_md"] is False
    c.post(f"/api/jobs/{job}/draft", json={"resume_md": "# B resume\n"})
    _switch(c, a)
    draft_a = c.get(f"/api/jobs/{job}/draft?include_markdown=true").json()
    assert draft_a["resume_md"].startswith("# A resume")
    assert (settings.applications_dir / f"profile-{b}" / str(job) / "resume.md").exists()


def test_pinned_profile_wins_over_the_active_one(setup):
    # A background task started for A keeps reading/writing A after a switch.
    c, engine, a, b, job = setup
    c.patch(f"/api/jobs/{job}/status", json={"status": "applied"})
    _switch(c, b)
    token = current_profile_id.set(a)
    try:
        with Session(engine) as s:
            assert session_profile_id(s) == a
            j = s.get(JobPosting, job)
            assert j.application is not None and j.application.status.value == "applied"
    finally:
        current_profile_id.reset(token)
    with Session(engine) as s:
        assert s.get(JobPosting, job).application is None


def test_running_task_dedupe_is_per_profile():
    state = tasks.TaskState(id="t-a", kind="score_pending", total=1, profile_id=1)
    with tasks._lock:
        tasks._tasks["t-a"] = state
    try:
        assert tasks.active_task("score_pending", 1) is state
        assert tasks.active_task("score_pending", 2) is None
    finally:
        with tasks._lock:
            tasks._tasks.pop("t-a", None)
