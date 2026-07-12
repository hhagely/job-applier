from __future__ import annotations

import json
import re
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from job_applier import services
from job_applier.ai import scoring, tasks
from job_applier.api.app import app
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


@pytest.mark.parametrize(
    "status,expected_archived",
    [
        (None, True),  # no application row -> untriaged -> archived
        (ApplicationStatus.new, True),  # default status -> untriaged -> archived
        (ApplicationStatus.interested, False),
        (ApplicationStatus.applied, False),
        (ApplicationStatus.interviewing, False),
    ],
)
def test_score_pending_only_archives_untriaged_low_scorers(
    monkeypatch, status, expected_archived
):
    """A low re-score must not clobber a status the user set manually — only
    untriaged jobs (no row, or still ``new``) are auto-archived."""
    monkeypatch.setattr(scoring.providers, "run", lambda *a, **k: LOW)
    e = _engine()
    with Session(e) as s:
        _seed_resume(s)
        job = _seed_job(s, title="LowFit Engineer")
        if status is not None:
            services.bulk_set_status(s, [job.id], status)
        outcomes = scoring.score_pending(s, provider="claude")
        assert outcomes[0].score == 40  # below ARCHIVE_BELOW
        s.refresh(job)
        assert job.application is not None
        if expected_archived:
            assert job.application.status == ApplicationStatus.archived
        else:
            assert job.application.status == status  # preserved untouched


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


# ---- batch scoring --------------------------------------------------------


def _batch_ids(prompt: str) -> list[int]:
    """Job ids embedded in a batch prompt's JOBS_BLOCK (each appears twice)."""
    seen: list[int] = []
    for m in re.findall(r"JOB id=(\d+)", prompt):
        i = int(m)
        if i not in seen:
            seen.append(i)
    return seen


def _emit_batch_ids(ids: list[int], score: int = 82) -> str:
    # Empty rubric -> _reconcile_score falls back to the top-level score verbatim.
    results = [{"id": i, "score": score, "rubric": {}, "reasoning": "ok"} for i in ids]
    return json.dumps({"results": results})


def _emit_batch(prompt: str, score: int = 82) -> str:
    return _emit_batch_ids(_batch_ids(prompt), score)


def _is_batch(prompt: str) -> bool:
    return "JOB id=" in prompt


def test_chunk_jobs_caps_by_count():
    jobs = [SimpleNamespace(description="x" * 10) for _ in range(10)]
    chunks = scoring.chunk_jobs(jobs)
    assert [len(c) for c in chunks] == [scoring.BATCH_MAX_JOBS, 2]
    assert sum(len(c) for c in chunks) == 10


def test_chunk_jobs_isolates_oversized_jd():
    small = SimpleNamespace(description="x" * 10)
    big = SimpleNamespace(description="x" * (scoring.BATCH_JD_CHAR_BUDGET + 1))
    # The over-budget JD lands in a batch of one (its own single-job call), losing
    # no content; the surrounding small jobs are not merged into it.
    chunks = scoring.chunk_jobs([small, big, small])
    assert [len(c) for c in chunks] == [1, 1, 1]


def test_score_pending_batches_in_one_call(monkeypatch):
    """Four jobs are scored in a SINGLE batch invocation (resume+rubric sent once)."""
    calls = {"n": 0}

    def _run(provider, prompt, **k):
        calls["n"] += 1
        assert _is_batch(prompt), "expected the batch prompt, not a single-job call"
        return _emit_batch(prompt, score=82)

    monkeypatch.setattr(scoring.providers, "run", _run)
    e = _engine()
    with Session(e) as s:
        _seed_resume(s)
        for i in range(4):
            _seed_job(s, title=f"Senior Engineer {i}")
        outcomes = scoring.score_pending(s, provider="claude")

    assert calls["n"] == 1  # one call for all four jobs
    assert len(outcomes) == 4 and {o.score for o in outcomes} == {82}
    assert all(o.error is None for o in outcomes)


def test_score_pending_batch_gap_falls_back_to_single_job(monkeypatch):
    """A job the batch drops is re-scored one at a time — never lost."""
    calls = {"batch": 0, "single": 0}

    def _run(provider, prompt, **k):
        if _is_batch(prompt):
            calls["batch"] += 1
            ids = _batch_ids(prompt)
            return _emit_batch_ids(ids[:-1], score=82)  # omit the last job
        calls["single"] += 1
        return CANNED  # single-job fallback

    monkeypatch.setattr(scoring.providers, "run", _run)
    e = _engine()
    with Session(e) as s:
        _seed_resume(s)
        for i in range(4):
            _seed_job(s, title=f"Senior Engineer {i}")
        outcomes = scoring.score_pending(s, provider="claude")

    assert calls["batch"] == 1 and calls["single"] == 1  # one gap refilled single-job
    assert len(outcomes) == 4 and {o.score for o in outcomes} == {82}


def test_score_pending_whole_batch_failure_falls_back(monkeypatch):
    """A batch call that never yields valid JSON degrades to single-job for all of it."""

    def _run(provider, prompt, **k):
        if _is_batch(prompt):
            return "not json at all"  # fails both attempts -> batch discarded
        return CANNED

    monkeypatch.setattr(scoring.providers, "run", _run)
    e = _engine()
    with Session(e) as s:
        _seed_resume(s)
        for i in range(3):
            _seed_job(s, title=f"Senior Engineer {i}")
        outcomes = scoring.score_pending(s, provider="claude")

    assert len(outcomes) == 3 and {o.score for o in outcomes} == {82}
    assert all(o.error is None for o in outcomes)


def test_score_pending_batch_low_scores_auto_archive(monkeypatch):
    """Auto-archive of untriaged sub-60 jobs works on the batch path too."""

    def _run(provider, prompt, **k):
        assert _is_batch(prompt)
        return _emit_batch(prompt, score=40)

    monkeypatch.setattr(scoring.providers, "run", _run)
    e = _engine()
    with Session(e) as s:
        _seed_resume(s)
        a = _seed_job(s, title="Senior Engineer A")
        b = _seed_job(s, title="Senior Engineer B")
        outcomes = scoring.score_pending(s, provider="claude")
        assert {o.score for o in outcomes} == {40}
        for job in (a, b):
            s.refresh(job)
            assert job.application is not None
            assert job.application.status == ApplicationStatus.archived


def test_build_batch_score_prompt_tags_each_job_by_id():
    e = _engine()
    with Session(e) as s:
        j1 = _seed_job(s, title="Senior One", desc="<p>TypeScript.</p>")
        j2 = _seed_job(s, title="Senior Two", desc="<p>React.</p>")
        prompt = scoring.build_batch_score_prompt("my resume", [j1, j2])
    assert f"JOB id={j1.id}" in prompt and f"JOB id={j2.id}" in prompt
    assert "Senior One" in prompt and "Senior Two" in prompt
    assert "my resume" in prompt
    # JD HTML is flattened, and the resume appears once (amortized).
    assert "<p>" not in prompt and prompt.count("my resume") == 1


# ---- state-rule clause ({{STATE_RULE}} plumbing) --------------------------


def test_state_rule_clause_present_only_when_home_state_set():
    # With a home state the clause names it; without one it's empty (the rule
    # simply doesn't appear, matching the ingest filter which skips it too).
    clause = scoring._state_rule_clause("Missouri")
    assert "state allow-list excludes Missouri" in clause
    assert scoring._state_rule_clause(None) == ""
    assert scoring._state_rule_clause("") == ""


@pytest.mark.parametrize("builder", ["single", "batch"])
def test_score_prompt_includes_state_rule_when_home_state_set(builder):
    e = _engine()
    with Session(e) as s:
        job = _seed_job(s, title="Senior Engineer", desc="<p>TypeScript.</p>")
        if builder == "single":
            with_state = scoring.build_score_prompt("resume", job, home_state="Missouri")
            without = scoring.build_score_prompt("resume", job)
        else:
            with_state = scoring.build_batch_score_prompt("resume", [job], home_state="Missouri")
            without = scoring.build_batch_score_prompt("resume", [job])
    # Set: the home-state hard rule is rendered by name.
    assert "state allow-list excludes Missouri" in with_state
    # Unset: no state rule, and the placeholder is always resolved (never leaked
    # as a literal into the prompt the model sees).
    assert "state allow-list excludes" not in without
    assert "{{STATE_RULE}}" not in with_state
    assert "{{STATE_RULE}}" not in without


# ---- services parity (guards the refactor) --------------------------------


def test_upsert_score_service_matches_endpoint():
    """The service and the HTTP route produce identical persisted state."""
    e1, e2 = _engine(), _engine()

    # Via service.
    with Session(e1) as s:
        _seed_resume(s)
        job = _seed_job(s)
        services.upsert_score(s, job.id, score=77, rubric={"a": 1}, reasoning="via svc")
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


# ---- event stream + dedupe (event-driven progress) ------------------------


def test_task_broker_publishes_snapshots_to_subscribers():
    """publish() fans a JSON-safe snapshot (incl. ref) to every subscriber, and
    unsubscribe() stops delivery."""
    received: list[dict] = []
    cb = received.append  # bind ONCE — a fresh `received.append` wouldn't unsubscribe
    tasks.subscribe(cb)
    try:
        state = tasks.TaskState(id="x1", kind="score_pending", total=3, done=1, ref="7")
        tasks.publish(state)
    finally:
        tasks.unsubscribe(cb)

    assert received and received[0]["id"] == "x1"
    assert received[0]["kind"] == "score_pending"
    assert received[0]["done"] == 1 and received[0]["total"] == 3
    assert received[0]["ref"] == "7"
    assert received[0]["status"] == "running"


def test_task_broker_unsubscribe_stops_delivery():
    received: list[dict] = []
    cb = received.append
    tasks.subscribe(cb)
    tasks.publish(tasks.TaskState(id="a", kind="ingest", total=1))
    tasks.unsubscribe(cb)
    tasks.publish(tasks.TaskState(id="b", kind="ingest", total=1))
    assert [r["id"] for r in received] == ["a"]


def test_score_pending_endpoint_dedupes_concurrent_runs(client, monkeypatch):
    """A second start while one score run is in flight returns the SAME task id
    instead of queueing a duplicate scan of the pending queue."""
    import threading

    c, e = client
    gate = threading.Event()
    with Session(e) as s:
        set_setting(s, "ai_provider", "claude")
        _seed_resume(s)
        _seed_job(s, title="Senior Engineer")

    monkeypatch.setattr(scoring, "open_session", lambda: Session(e))

    def _blocking_run(*a, **k):
        gate.wait(5)  # hold the worker so the task stays "running"
        return CANNED

    monkeypatch.setattr(scoring.providers, "run", _blocking_run)

    try:
        first = c.post("/api/ai/score-pending", json={}).json()["task_id"]
        second = c.post("/api/ai/score-pending", json={}).json()["task_id"]
        assert first == second
    finally:
        gate.set()  # release so the worker thread finishes (no teardown hang)

    for _ in range(250):
        if c.get(f"/api/ai/tasks/{first}").json()["status"] != "running":
            break
        time.sleep(0.02)


def test_task_events_generator_replays_running_task():
    """The /api/ai/events generator replays any in-flight task to a freshly
    connected subscriber (so progress re-attaches without a poll) and emits the
    ``: connected`` marker.

    Driven directly rather than through TestClient streaming: TestClient buffers a
    streaming response and this endpoint's generator is intentionally endless
    (keepalive loop), so a fixed, bounded read of the async generator is both
    deterministic and hang-proof.
    """
    import asyncio

    from job_applier.api import ai as ai_mod

    # Register a running task straight into the registry (no worker/AI needed).
    state = tasks.TaskState(id="ev-smoke", kind="ingest", total=2, done=1)
    with tasks._lock:
        tasks._tasks[state.id] = state

    class _Req:
        async def is_disconnected(self):  # only consulted past the replay we read
            return True

    async def drive():
        resp = await ai_mod.task_events(_Req())
        agen = resp.body_iterator
        chunks: list[str] = []
        try:
            for _ in range(50):  # replay lines then ": connected" — bounded
                chunk = await agen.__anext__()
                chunks.append(chunk)
                if chunk.startswith(": connected"):
                    break
        finally:
            await agen.aclose()  # runs the endpoint's finally -> unsubscribe
        return chunks

    try:
        chunks = asyncio.run(drive())
    finally:
        with tasks._lock:
            tasks._tasks.pop("ev-smoke", None)

    joined = "".join(chunks)
    assert "ev-smoke" in joined  # the running task was replayed
    assert '"kind": "ingest"' in joined
    assert any(c.startswith(": connected") for c in chunks)
