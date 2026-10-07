"""Scrape once, match per profile.

Ingest stores every posting that passes the shared rules (remote, US, sales,
crypto) once; ``matching`` then records each profile's own verdict. These cover
the guarantees that used to live at ingest time (blacklist, personal filter)
at their new home, plus what scrape-once adds: one scrape fills every
profile's queue, and a new or edited profile is matched without re-scraping.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlmodel import Session, select

from job_applier import blacklist, ingest, matching
from job_applier.ai import tasks
from job_applier.config import settings
from job_applier.filters import build_config, title_quick_fail, union_title_config
from job_applier.filters.rules import _BUILTIN_DEFAULT
from job_applier.models import db
from job_applier.models.db import (
    Application,
    ApplicationStatus,
    FilterStatus,
    JobPosting,
    JobProfileLink,
    SearchProfile,
)
from job_applier.sources.base import RawJob


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    """A real file DB per test: matching opens its own sessions on ``engine()``."""
    monkeypatch.setattr(settings, "db_path", tmp_path / "jobs.db")
    monkeypatch.setattr(db, "_engine", None)
    db.create_db_and_tables()
    yield db.engine()
    monkeypatch.setattr(db, "_engine", None)


def _raw(i: int, **overrides) -> RawJob:
    base = dict(
        source="test",
        source_id=f"t-{i}",
        url=f"https://example.com/{i}",
        title="Senior Software Engineer",
        company_name=f"Co{i}",
        description="We build with TypeScript and React.",
        location="Remote - US",
        remote=True,
    )
    base.update(overrides)
    return RawJob(**base)


def _profile(engine, name: str, **criteria) -> int:
    with Session(engine) as s:
        p = SearchProfile(name=name, is_active=not s.exec(select(SearchProfile)).first(), **criteria)
        s.add(p)
        s.commit()
        return p.id


def _store(engine, *raws: RawJob) -> list[int]:
    """Run raws through ingest's write path (shared rules only)."""
    stats = ingest.IngestStats()
    with Session(engine) as s:
        caches = ingest._IngestCaches.load(s)
        for raw in raws:
            ingest.ingest_one(s, raw, stats, caches=caches)
        s.commit()
    return caches.new_ids


def _verdicts(engine, profile_id: int) -> dict[int, FilterStatus]:
    with Session(engine) as s:
        rows = s.exec(
            select(JobProfileLink)
            .where(JobProfileLink.profile_id == profile_id)
            .execution_options(all_profiles=True)
        ).all()
    return {r.job_id: r.filter_status for r in rows}


# Captured at import, before conftest's autouse ``rematches`` stub replaces it.
_REAL_START_REMATCH = matching.start_rematch

TS = dict(seniority_terms=["senior"], required_tech=["typescript"])
RUST = dict(seniority_terms=["senior"], required_tech=["rust"])


def test_ingest_applies_only_the_shared_rules(fresh_db):
    stats = ingest.IngestStats()
    with Session(fresh_db) as s:
        caches = ingest._IngestCaches.load(s)
        # Fails a personal rule (no seniority term): still stored.
        ingest.ingest_one(s, _raw(1, title="Software Engineer"), stats, caches=caches)
        # Fails a shared rule (not remote): never stored.
        ingest.ingest_one(s, _raw(2, remote=False), stats, caches=caches)
        s.commit()
        titles = [j.title for j in s.exec(select(JobPosting)).all()]
    assert titles == ["Software Engineer"]
    assert stats.inserted == 1 and stats.dropped_filter == 1


def test_one_scrape_fills_each_profile_by_its_own_rules(fresh_db):
    ts = _profile(fresh_db, "Herb", **TS)
    rust = _profile(fresh_db, "Partner", **RUST)
    ts_job, rust_job = _store(
        fresh_db,
        _raw(1),
        _raw(2, title="Senior Systems Engineer", description="Rust services."),
    )
    matching.match_all_profiles()
    assert _verdicts(fresh_db, ts) == {ts_job: FilterStatus.passed, rust_job: FilterStatus.dropped}
    assert _verdicts(fresh_db, rust) == {ts_job: FilterStatus.dropped, rust_job: FilterStatus.passed}


def test_blacklist_drops_only_for_the_profile_that_blacklisted(fresh_db):
    a = _profile(fresh_db, "A", **TS)
    b = _profile(fresh_db, "B", **TS)
    with Session(fresh_db) as s:
        s.info["profile_id"] = a
        # A naming variant: "Globex" catches "Globex Inc".
        blacklist.add_blacklisted_company(s, "Globex")
    (job,) = _store(fresh_db, _raw(1, company_name="Globex Inc"))
    matching.match_all_profiles()
    assert _verdicts(fresh_db, a) == {job: FilterStatus.dropped}
    assert _verdicts(fresh_db, b) == {job: FilterStatus.passed}
    with Session(fresh_db) as s:
        link = s.get(JobProfileLink, (job, a))
    assert link.filter_reason == matching.BLACKLISTED_REASON


def test_a_rescrape_only_evaluates_new_postings(fresh_db):
    p = _profile(fresh_db, "A", **TS)
    _store(fresh_db, _raw(1))
    assert matching.match_profile(p).evaluated == 1
    assert matching.match_profile(p).evaluated == 0
    _store(fresh_db, _raw(2, title="Senior Platform Engineer"))
    assert matching.match_profile(p).evaluated == 1


def test_new_profile_is_matched_against_existing_postings_without_a_scrape(fresh_db):
    _profile(fresh_db, "A", **TS)
    (job,) = _store(fresh_db, _raw(1))
    matching.match_all_profiles()
    late = _profile(fresh_db, "Late", **TS)
    stats = matching.match_profile(late, rematch=True)
    assert stats.passed == 1
    assert _verdicts(fresh_db, late) == {job: FilterStatus.passed}


def test_rematch_applies_edited_criteria_but_keeps_jobs_already_acted_on(fresh_db):
    p = _profile(fresh_db, "A", **TS)
    applied_job, other_job, archived_job = _store(
        fresh_db, _raw(1), _raw(2, title="Senior Platform Engineer"), _raw(3, title="Senior Web Engineer")
    )
    matching.match_profile(p)
    with Session(fresh_db) as s:
        s.info["profile_id"] = p
        s.add(Application(job_id=applied_job, status=ApplicationStatus.applied))
        # Archived (e.g. auto-archived on a low score) was never pursued, so it isn't protected.
        s.add(Application(job_id=archived_job, status=ApplicationStatus.archived))
        prof = s.get(SearchProfile, p)
        prof.required_tech = ["rust"]  # now neither posting qualifies
        s.add(prof)
        s.commit()
    stats = matching.match_profile(p, rematch=True)
    assert stats.kept_acted == 1
    assert _verdicts(fresh_db, p) == {
        applied_job: FilterStatus.passed,  # you applied: it stays in your queue
        other_job: FilterStatus.dropped,
        archived_job: FilterStatus.dropped,
    }


def test_stale_postings_are_not_matched(fresh_db):
    p = _profile(fresh_db, "A", **TS)
    (job,) = _store(fresh_db, _raw(1))
    later = datetime.now(timezone.utc) + timedelta(days=matching.MATCH_WINDOW_DAYS + 1)
    assert matching.match_profile(p, now=later).evaluated == 0
    assert _verdicts(fresh_db, p) == {}
    assert job


def test_raw_is_trimmed_only_from_postings_nobody_matched(fresh_db):
    _profile(fresh_db, "A", **TS)
    kept, trimmed = _store(
        fresh_db,
        _raw(1, raw={"payload": "keep"}),
        _raw(2, title="Software Engineer", raw={"payload": "drop"}),
    )
    matching.match_all_profiles()
    assert ingest._trim_unmatched_raw([kept, trimmed]) == 1
    with Session(fresh_db) as s:
        assert s.get(JobPosting, kept).raw == {"payload": "keep"}
        trimmed_row = s.get(JobPosting, trimmed)
        assert trimmed_row.raw == {}
        # The description stays, so a profile added later can still match it.
        assert trimmed_row.description


def test_raw_is_kept_when_the_source_sent_no_description(fresh_db):
    # Empty description + empty raw is how matching recognises a pruned row;
    # trimming this one would hide it from every future profile.
    p = _profile(fresh_db, "A", **TS)
    (job,) = _store(fresh_db, _raw(1, title="Software Engineer", description="", raw={"id": 1}))
    matching.match_all_profiles()
    assert ingest._trim_unmatched_raw([job]) == 0
    other = _profile(fresh_db, "B", required_tech=["typescript"])
    with Session(fresh_db) as s:
        s.get(JobPosting, job).tags = ["TypeScript"]
        s.commit()
    assert matching.match_profile(other).evaluated == 1
    assert _verdicts(fresh_db, p) == {job: FilterStatus.dropped}


def test_title_preskip_only_skips_a_title_every_profile_rejects():
    senior = build_config(role_titles=[], seniority_terms=["senior"], required_tech=["ts"], excluded_tech=[])
    manager = build_config(role_titles=[], seniority_terms=["manager"], required_tech=["ts"], excluded_tech=[])
    union = union_title_config([senior, manager])
    assert not title_quick_fail("Engineering Manager", union)
    assert not title_quick_fail("Senior Engineer", union)
    assert title_quick_fail("Junior Engineer", union)
    # Sales titles are a shared rule: skipped regardless.
    assert title_quick_fail("Senior Account Executive", union)


def _cfg(**criteria):
    return build_config(
        role_titles=[], required_tech=[], excluded_tech=[],
        **{"seniority_terms": [], **criteria},
    )


def test_title_preskip_turns_a_gate_off_when_any_profile_lacks_it():
    # One profile with no seniority terms accepts any level, so nobody may skip on it.
    union = union_title_config([_cfg(seniority_terms=["senior"]), _cfg()])
    assert not title_quick_fail("Junior Engineer", union)
    # Title keywords union across the profiles that have them...
    union = union_title_config([_cfg(title_terms=["project manager"]), _cfg(title_terms=["engineer"])])
    assert not title_quick_fail("Senior Project Manager", union)
    assert not title_quick_fail("Senior Engineer", union)
    assert title_quick_fail("Marketing Coordinator", union)
    # ...and switch off when one profile has none.
    union = union_title_config([_cfg(title_terms=["project manager"]), _cfg(seniority_terms=["senior"])])
    assert not title_quick_fail("Senior Data Analyst", union)
    assert union_title_config([]) is _BUILTIN_DEFAULT


def test_start_rematch_runs_a_full_rematch_as_a_pinned_match_task(fresh_db, monkeypatch):
    started = {}

    def run_now(kind, total, fn, *, ref=None, profile_id=None):
        started.update(kind=kind, ref=ref, profile_id=profile_id)
        started["state"] = state = tasks.TaskState(id="t", kind=kind, total=total)
        fn(state)
        return "t"

    monkeypatch.setattr(tasks, "start_task", run_now)
    p = _profile(fresh_db, "A", **TS)
    (job,) = _store(fresh_db, _raw(1))
    matching.match_profile(p)
    with Session(fresh_db) as s:
        s.get(SearchProfile, p).required_tech = ["rust"]
        s.commit()
    assert _REAL_START_REMATCH(p) == "t"
    assert (started["kind"], started["ref"], started["profile_id"]) == ("match", str(p), p)
    # An already-judged posting is re-evaluated, which only rematch=True does.
    assert _verdicts(fresh_db, p) == {job: FilterStatus.dropped}
    assert started["state"].results == ["0 in your queue, 0 to review, 1 filtered out"]


def test_a_deleted_profile_gets_no_links(fresh_db):
    # A re-match queued before its profile was deleted must not write links
    # back for it (they'd undo delete_profile and land on a reused id).
    p = _profile(fresh_db, "A", **TS)
    _store(fresh_db, _raw(1))
    with Session(fresh_db) as s:
        s.delete(s.get(SearchProfile, p))
        s.commit()
    assert matching.match_profile(p, rematch=True) == matching.MatchStats()
    assert _verdicts(fresh_db, p) == {}


def test_postings_without_a_description_are_matched_but_pruned_ones_are_not(fresh_db):
    p = _profile(fresh_db, "A", **TS)
    no_desc, pruned = _store(
        fresh_db,
        # The source sent no description; its tags still carry the stack.
        _raw(1, description="", tags=["TypeScript"], raw={"id": 1}),
        _raw(2, title="Senior Platform Engineer"),
    )
    with Session(fresh_db) as s:
        row = s.get(JobPosting, pruned)
        row.description, row.raw = "", {}  # what prune leaves behind
        s.add(row)
        s.commit()
    assert matching.match_profile(p).evaluated == 1
    assert _verdicts(fresh_db, p) == {no_desc: FilterStatus.passed}


class _Source:
    name = "fake"

    def __init__(self, *jobs: RawJob) -> None:
        self._jobs = jobs

    def fetch(self):
        return iter(self._jobs)


def test_a_scrape_runs_every_profile_through_matching(fresh_db):
    # run_ingest's tail: every profile gets verdicts on what was just stored,
    # match_cb reports each profile, and nobody-matched raw payloads are trimmed.
    a = _profile(fresh_db, "A", **TS)
    b = _profile(fresh_db, "B", **RUST)
    reported: list[str] = []
    ingest.run_ingest(
        sources=[_Source(_raw(1, raw={"p": 1}), _raw(2, title="Software Engineer", raw={"p": 2}))],
        match_cb=lambda name, _stats: reported.append(name),
    )
    with Session(fresh_db) as s:
        ts_job, unmatched = (
            s.exec(select(JobPosting.id).where(JobPosting.source_id == sid)).one()
            for sid in ("t-1", "t-2")
        )
        assert s.get(JobPosting, ts_job).raw == {"p": 1}
        assert s.get(JobPosting, unmatched).raw == {}
    assert reported == ["A", "B"]
    assert _verdicts(fresh_db, a) == {ts_job: FilterStatus.passed, unmatched: FilterStatus.dropped}
    assert _verdicts(fresh_db, b) == {ts_job: FilterStatus.dropped, unmatched: FilterStatus.dropped}


def test_rematch_reaches_old_postings_already_in_the_queue(fresh_db):
    # Blacklisting a company must hide its old postings too, not only the last
    # MATCH_WINDOW_DAYS: those still sit in the queue with a "passed" verdict.
    p = _profile(fresh_db, "A", **TS)
    (job,) = _store(fresh_db, _raw(1, company_name="Initech"))
    matching.match_profile(p)
    with Session(fresh_db) as s:
        old = s.get(JobPosting, job)
        old.posted_at = old.ingested_at = datetime.now(timezone.utc) - timedelta(
            days=matching.MATCH_WINDOW_DAYS + 10
        )
        s.add(old)
        s.info["profile_id"] = p
        blacklist.add_blacklisted_company(s, "Initech")
        s.commit()
    matching.match_profile(p, rematch=True)
    assert _verdicts(fresh_db, p) == {job: FilterStatus.dropped}


def test_a_kept_verdict_keeps_its_reason(fresh_db):
    # A manual verdict on a job you applied to stays manual, banner reason intact.
    p = _profile(fresh_db, "A", **TS)
    (job,) = _store(fresh_db, _raw(1))
    with Session(fresh_db) as s:
        s.add(JobProfileLink(job_id=job, profile_id=p, filter_status=FilterStatus.manual,
                             filter_reason="tech only implied"))
        s.info["profile_id"] = p
        s.add(Application(job_id=job, status=ApplicationStatus.applied))
        s.get(SearchProfile, p).required_tech = ["rust"]
        s.commit()
    matching.match_profile(p, rematch=True)
    with Session(fresh_db) as s:
        link = s.exec(select(JobProfileLink).execution_options(all_profiles=True)).one()
    assert (link.filter_status, link.filter_reason) == (FilterStatus.manual, "tech only implied")


def test_rematch_blacklists_pruned_postings_but_keeps_their_other_verdicts(fresh_db):
    # A pruned posting has only its company left to judge.
    p = _profile(fresh_db, "A", **TS)
    blocked, kept = _store(fresh_db, _raw(1, company_name="Initech"), _raw(2, company_name="Hooli"))
    matching.match_profile(p)
    with Session(fresh_db) as s:
        for job in (blocked, kept):
            row = s.get(JobPosting, job)
            row.description, row.raw = "", {}  # what prune leaves behind
        s.info["profile_id"] = p
        blacklist.add_blacklisted_company(s, "Initech")
        s.commit()
    stats = matching.match_profile(p, rematch=True)
    assert stats.evaluated == 2
    assert _verdicts(fresh_db, p) == {blocked: FilterStatus.dropped, kept: FilterStatus.passed}


def test_a_profile_deleted_mid_run_stops_getting_links(fresh_db, monkeypatch):
    monkeypatch.setattr(matching, "MATCH_BATCH", 1)
    p = _profile(fresh_db, "A", **TS)
    _store(fresh_db, _raw(1), _raw(2))

    def delete_after_first_batch(done: int, _total: int) -> None:
        if done == 1:
            with Session(fresh_db) as s:
                s.delete(s.get(SearchProfile, p))
                s.commit()

    stats = matching.match_profile(p, progress_cb=delete_after_first_batch)
    assert stats.evaluated == 2  # the second batch was judged, then not written
    assert len(_verdicts(fresh_db, p)) == 1


def test_one_profiles_matching_failure_does_not_stop_the_others(fresh_db, monkeypatch):
    a = _profile(fresh_db, "A", **TS)
    b = _profile(fresh_db, "B", **TS)
    (job,) = _store(fresh_db, _raw(1))
    real = matching.match_profile

    def flaky(pid, **kw):
        if pid == a:
            raise RuntimeError("database is locked")
        return real(pid, **kw)

    monkeypatch.setattr(matching, "match_profile", flaky)
    out = matching.match_all_profiles()
    assert set(out) == {b}
    assert _verdicts(fresh_db, b) == {job: FilterStatus.passed}
    assert _verdicts(fresh_db, a) == {}  # unlinked, so the next run retries it
