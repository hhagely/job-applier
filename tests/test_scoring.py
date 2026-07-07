from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from job_applier.ai import scoring, tasks
from job_applier.api import services
from job_applier.api.app import app
from job_applier.api.schemas import ScoreIn
from job_applier.models.db import (
    JobPosting,
    MatchScore,
    MatchScoreHistory,
    Resume,
    get_session,
    set_setting,
)
from job_applier.models.db import ApplicationStatus, FilterStatus

CANNED = (
    '{"score": 82, "rubric": {"skills_overlap": {"points": 26, "note": "x"}, '
    '"experience_match": {"points": 22, "note": "y"}, "role_fit": {"points": 16, "note": "z"}, '
    '"domain_fit": {"points": 8, "note": "d"}, "hard_requirements": {"points": 10, "note": "h"}}, '
    '"reasoning": "Solid overlap."}'
)

# Internally-consistent low score (buckets sum to 40, matching the top-level score
# so the bucket-sum reconciliation leaves it below the archive threshold).
LOW = (
    '{"score": 40, "rubric": {"skills_overlap": {"points": 8, "note": "x"}, '
    '"experience_match": {"points": 10, "note": "y"}, "role_fit": {"points": 10, "note": "z"}, '
    '"domain_fit": {"points": 6, "note": "d"}, "hard_requirements": {"points": 6, "note": "h"}}, '
    '"reasoning": "Weak overlap."}'
)


def _engine():
    e = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(e)
    return e


def _seed_resume(session, *, active=True, text="TypeScript, React, Node.js, 17 years"):
    r = Resume(original_filename="r.pdf", pdf_path="/tmp/r.pdf", extracted_text=text, is_active=active)
    session.add(r)
    session.commit()
    session.refresh(r)
    return r


def _seed_job(session, *, title="Senior Engineer", desc="<p>We use TypeScript.</p>"):
    j = JobPosting(
        source="test",
        source_id=f"t-{title}",
        url="https://e.com/1",
        title=title,
        description=desc,
        dedupe_hash=f"h-{title}",
        filter_status=FilterStatus.passed,
    )
    session.add(j)
    session.commit()
    session.refresh(j)
    return j


# ---- prompt ---------------------------------------------------------------


def test_build_score_prompt_includes_job_fields():
    e = _engine()
    with Session(e) as s:
        job = _seed_job(s, title="Staff Platform Engineer", desc="<p>Rust &amp; Go.</p>")
        prompt = scoring.build_score_prompt("my resume text", job)
    assert "my resume text" in prompt
    assert "Staff Platform Engineer" in prompt
    # HTML flattened + entities unescaped.
    assert "Rust & Go." in prompt
    assert "<p>" not in prompt
    # Template rubric survived.
    assert "skills_overlap" in prompt


# ---- score_one ------------------------------------------------------------


def test_score_one_persists_and_snapshots_history(monkeypatch):
    e = _engine()
    monkeypatch.setattr(scoring.providers, "run", lambda *a, **k: CANNED)
    with Session(e) as s:
        _seed_resume(s)
        job = _seed_job(s)
        result = scoring.score_one(s, "claude", "resume text", job)
        assert result.score == 82

        score = s.exec(select(MatchScore).where(MatchScore.job_id == job.id)).one()
        assert score.score == 82
        assert score.scored_by == "claude-cli"
        assert score.score_kind == "baseline"
        assert score.resume_id is not None  # stamped with active resume

        # Re-score snapshots the prior value to history.
        scoring.score_one(s, "claude", "resume text", job)
        hist = s.exec(select(MatchScoreHistory).where(MatchScoreHistory.job_id == job.id)).all()
        assert len(hist) == 1


def test_score_one_reconciles_score_from_bucket_sum(monkeypatch):
    # Top-level score disagrees with bucket sum (26+22+16+8+10=82) by > 5.
    bad_total = CANNED.replace('"score": 82', '"score": 50')
    monkeypatch.setattr(scoring.providers, "run", lambda *a, **k: bad_total)
    e = _engine()
    with Session(e) as s:
        _seed_resume(s)
        job = _seed_job(s)
        result = scoring.score_one(s, "claude", "resume", job)
        assert result.score == 82  # re-derived from buckets


def test_score_one_retries_then_flags_on_bad_json(monkeypatch):
    calls = {"n": 0}

    def _run(*a, **k):
        calls["n"] += 1
        return "sorry, I cannot produce JSON"

    monkeypatch.setattr(scoring.providers, "run", _run)
    e = _engine()
    with Session(e) as s:
        _seed_resume(s)
        job = _seed_job(s)
        with pytest.raises(scoring.ScoringError):
            scoring.score_one(s, "claude", "resume", job)
    assert calls["n"] == 2  # original + one retry


def test_score_one_retry_succeeds_on_second_attempt(monkeypatch):
    outputs = iter(["garbage", CANNED])
    monkeypatch.setattr(scoring.providers, "run", lambda *a, **k: next(outputs))
    e = _engine()
    with Session(e) as s:
        _seed_resume(s)
        job = _seed_job(s)
        result = scoring.score_one(s, "claude", "resume", job)
    assert result.score == 82


# ---- score_pending --------------------------------------------------------


def test_score_pending_auto_archives_below_60(monkeypatch):
    def _run(provider, prompt, **k):
        # Route by which title is in the prompt.
        return LOW if "LowFit" in prompt else CANNED

    monkeypatch.setattr(scoring.providers, "run", _run)
    e = _engine()
    with Session(e) as s:
        _seed_resume(s)
        good = _seed_job(s, title="Senior Engineer")
        bad = _seed_job(s, title="LowFit Engineer")
        outcomes = scoring.score_pending(s, provider="claude")
        assert {o.job_id for o in outcomes} == {good.id, bad.id}

        # Low scorer archived; good one not.
        s.refresh(bad)
        s.refresh(good)
        assert bad.application is not None and bad.application.status == ApplicationStatus.archived
        assert good.application is None or good.application.status != ApplicationStatus.archived


def test_score_pending_requires_active_resume(monkeypatch):
    monkeypatch.setattr(scoring.providers, "run", lambda *a, **k: CANNED)
    e = _engine()
    with Session(e) as s:
        _seed_job(s)  # no resume seeded
        with pytest.raises(scoring.NoActiveResume):
            scoring.score_pending(s, provider="claude")


def test_score_pending_survives_single_job_failure(monkeypatch):
    def _run(provider, prompt, **k):
        return "garbage" if "Broken" in prompt else CANNED

    monkeypatch.setattr(scoring.providers, "run", _run)
    e = _engine()
    with Session(e) as s:
        _seed_resume(s)
        ok = _seed_job(s, title="Senior Engineer")
        broken = _seed_job(s, title="Broken Engineer")
        ok_id, broken_id = ok.id, broken.id
        outcomes = {o.job_id: o for o in scoring.score_pending(s, provider="claude")}
    assert outcomes[ok_id].score == 82 and outcomes[ok_id].error is None
    assert outcomes[broken_id].score is None and outcomes[broken_id].error is not None


# ---- services parity (guards the refactor) --------------------------------


def test_upsert_score_service_matches_endpoint():
    """The service and the HTTP route produce identical persisted state."""
    e1, e2 = _engine(), _engine()

    # Via service.
    with Session(e1) as s:
        _seed_resume(s)
        job = _seed_job(s)
        services.upsert_score(s, job.id, ScoreIn(score=77, rubric={"a": 1}, reasoning="via svc"))
        svc_score = s.exec(select(MatchScore)).one()

    # Via HTTP endpoint (same engine-shaped setup).
    def _dep():
        with Session(e2) as s:
            yield s

    app.dependency_overrides[get_session] = _dep
    with Session(e2) as s:
        _seed_resume(s)
        job2 = _seed_job(s)
    with TestClient(app) as c:
        c.post(f"/api/jobs/{job2.id}/score", json={"score": 77, "rubric": {"a": 1}, "reasoning": "via svc"})
    app.dependency_overrides.clear()
    with Session(e2) as s:
        http_score = s.exec(select(MatchScore)).one()

    assert svc_score.score == http_score.score == 77
    assert svc_score.rubric == http_score.rubric
    assert svc_score.resume_id is not None and http_score.resume_id is not None


# ---- tasks registry -------------------------------------------------------


def test_start_task_runs_and_completes():
    def _fn(state):
        state.done = state.total
        state.results.append("ok")

    tid = tasks.start_task("unit", 3, _fn)
    for _ in range(50):
        st = tasks.get_task(tid)
        if st.status != "running":
            break
        time.sleep(0.02)
    st = tasks.get_task(tid)
    assert st.status == "done" and st.done == 3 and st.results == ["ok"]


def test_start_task_records_fatal_error():
    def _fn(state):
        raise RuntimeError("boom")

    tid = tasks.start_task("unit", 1, _fn)
    for _ in range(50):
        if tasks.get_task(tid).status != "running":
            break
        time.sleep(0.02)
    st = tasks.get_task(tid)
    assert st.status == "error" and any("boom" in e for e in st.errors)


# ---- endpoints ------------------------------------------------------------


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


def test_score_pending_endpoint_requires_provider(client):
    c, e = client
    with Session(e) as s:
        _seed_resume(s)
        _seed_job(s)
    r = c.post("/api/ai/score-pending", json={})
    assert r.status_code == 409
    assert "provider" in r.json()["detail"].lower()


def test_score_pending_endpoint_requires_resume(client):
    c, e = client
    with Session(e) as s:
        set_setting(s, "ai_provider", "claude")
        _seed_job(s)  # no resume
    r = c.post("/api/ai/score-pending", json={})
    assert r.status_code == 409
    assert "resume" in r.json()["detail"].lower()


def test_score_pending_endpoint_runs_task_end_to_end(client, monkeypatch, tmp_path):
    c, e = client
    with Session(e) as s:
        set_setting(s, "ai_provider", "claude")
        _seed_resume(s)
        job = _seed_job(s, title="Senior Engineer")
        job_id = job.id

    # The background worker opens its own session; point it at the same engine.
    monkeypatch.setattr(scoring, "open_session", lambda: Session(e))
    monkeypatch.setattr(scoring.providers, "run", lambda *a, **k: CANNED)

    start = c.post("/api/ai/score-pending", json={})
    assert start.status_code == 200
    tid = start.json()["task_id"]

    for _ in range(250):
        snap = c.get(f"/api/ai/tasks/{tid}").json()
        if snap["status"] != "running":
            break
        time.sleep(0.02)
    assert snap["status"] == "done"
    assert snap["total"] == 1 and snap["done"] == 1
    assert any("82/100" in line for line in snap["results"])

    # Score persisted through the normal path.
    with Session(e) as s:
        score = s.exec(select(MatchScore).where(MatchScore.job_id == job_id)).one()
        assert score.score == 82 and score.scored_by == "claude-cli"


def test_get_task_404_for_unknown_id(client):
    c, _e = client
    assert c.get("/api/ai/tasks/nope").status_code == 404
