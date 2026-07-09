from __future__ import annotations

import time

from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from job_applier import ingest
from job_applier.api.app import app
from job_applier.models.db import JobPosting, get_session
from job_applier.sources.base import RawJob


class FakeSource:
    def __init__(self, name, jobs):
        self.name = name
        self._jobs = jobs

    def fetch(self):
        return iter(self._jobs)


def _raw(sid, title="Senior Software Engineer", company="Acme"):
    return RawJob(
        source="fake",
        source_id=sid,
        url=f"https://example.com/{sid}",
        title=title,
        company_name=company,
        description="We use TypeScript and React on Node.js.",
        location="Remote — US",
        remote=True,
    )


class _BoomSource:
    """A source that yields one row and then raises mid-fetch."""

    name = "boom"

    def fetch(self):
        yield _raw("boom-1", company="Boom Co")
        raise ValueError("simulated bad payload from a flaky source")


def _engine():
    e = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(e)
    return e


# ---- run_ingest progress callback -----------------------------------------


def test_run_ingest_reports_per_source_progress(monkeypatch):
    e = _engine()
    monkeypatch.setattr(ingest, "engine", lambda: e)
    calls = []
    sources = [
        FakeSource("alpha", [_raw("a1")]),
        FakeSource("beta", [_raw("b1"), _raw("b2")]),
    ]
    stats = ingest.run_ingest(
        sources=sources,
        progress_cb=lambda done, total, name, s: calls.append((done, total, name, s.inserted)),
    )
    # Fired once per source, in order, with (done, total, name).
    assert [(c[0], c[1], c[2]) for c in calls] == [(1, 2, "alpha"), (2, 2, "beta")]
    # Stats are cumulative and match a real ingest.
    assert stats.fetched == 3
    assert stats.inserted >= 1
    assert calls[1][3] >= calls[0][3]


def test_run_ingest_isolates_a_failing_source(monkeypatch):
    """One source raising mid-fetch is logged and skipped — the sources before and
    after it still persist, and the failed source's partial rows/stats are dropped."""
    e = _engine()
    monkeypatch.setattr(ingest, "engine", lambda: e)
    seen = []
    sources = [
        FakeSource("alpha", [_raw("a1", company="Alpha Co")]),
        _BoomSource(),
        FakeSource("gamma", [_raw("g1", company="Gamma Co")]),
    ]
    stats = ingest.run_ingest(
        sources=sources,
        progress_cb=lambda done, total, name, s: seen.append((done, total, name)),
    )
    # Progress still fires for every source, including the one that failed.
    assert seen == [(1, 3, "alpha"), (2, 3, "boom"), (3, 3, "gamma")]
    # The good sources persisted; the failing source's partial row was rolled back.
    with Session(e) as s:
        companies = {j.company.name for j in s.exec(select(JobPosting)).all()}
    assert companies == {"Alpha Co", "Gamma Co"}
    # Stats reflect only the two good rows (the failed source's counts were restored).
    assert stats.inserted == 2


def test_run_ingest_without_cb_is_unchanged(monkeypatch):
    e = _engine()
    monkeypatch.setattr(ingest, "engine", lambda: e)
    stats = ingest.run_ingest(sources=[FakeSource("only", [_raw("x1")])])
    assert stats.fetched == 1
    assert stats.inserted == 1


# ---- endpoint -------------------------------------------------------------


def test_ingest_endpoint_starts_and_runs_task(monkeypatch):
    e = _engine()

    def _dep():
        with Session(e) as s:
            yield s

    app.dependency_overrides[get_session] = _dep

    # Avoid real network/sources: 2 sources for the total, a fake run that drives
    # the progress callback the task wires up.
    monkeypatch.setattr("job_applier.sources.get_all_sources", lambda: [object(), object()])

    def _fake_run(sources=None, progress_cb=None):
        for i, name in enumerate(["alpha", "beta"], start=1):
            if progress_cb:
                progress_cb(i, 2, name, ingest.IngestStats(inserted=i, passed_filter=i, fetched=i))
        return ingest.IngestStats(inserted=2, passed_filter=2, fetched=5)

    monkeypatch.setattr(ingest, "run_ingest", _fake_run)

    with TestClient(app) as c:
        start = c.post("/api/ingest")
        assert start.status_code == 200
        tid = start.json()["task_id"]
        for _ in range(250):
            snap = c.get(f"/api/ai/tasks/{tid}").json()
            if snap["status"] != "running":
                break
            time.sleep(0.02)
    app.dependency_overrides.clear()

    assert snap["status"] == "done"
    assert snap["total"] == 2 and snap["done"] == 2
    # Per-source lines plus a final summary.
    assert any(r.startswith("alpha:") for r in snap["results"])
    assert any(r.startswith("done:") for r in snap["results"])


def test_ingest_endpoint_persists_via_real_run(monkeypatch):
    """End-to-end through the real run_ingest with fake sources (no network)."""
    e = _engine()

    def _dep():
        with Session(e) as s:
            yield s

    app.dependency_overrides[get_session] = _dep
    sources = [FakeSource("alpha", [_raw("a1"), _raw("a2", title="Staff Engineer")])]
    # The endpoint's total uses job_applier.sources.get_all_sources (imported at
    # call time); run_ingest uses the name bound in its own module namespace.
    monkeypatch.setattr("job_applier.sources.get_all_sources", lambda: sources)
    monkeypatch.setattr(ingest, "get_all_sources", lambda: sources)
    monkeypatch.setattr(ingest, "engine", lambda: e)

    with TestClient(app) as c:
        tid = c.post("/api/ingest").json()["task_id"]
        for _ in range(250):
            snap = c.get(f"/api/ai/tasks/{tid}").json()
            if snap["status"] != "running":
                break
            time.sleep(0.02)
    app.dependency_overrides.clear()

    assert snap["status"] == "done"
    with Session(e) as s:
        jobs = s.exec(select(JobPosting)).all()
        assert len(jobs) == 2
