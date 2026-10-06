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

from job_applier import ingest, matching, services
from job_applier.config import settings
from job_applier.filters import build_config, title_quick_fail, union_title_config
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
        services.add_blacklisted_company(s, "Globex")
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
    applied_job, other_job = _store(
        fresh_db, _raw(1), _raw(2, title="Senior Platform Engineer")
    )
    matching.match_profile(p)
    with Session(fresh_db) as s:
        s.info["profile_id"] = p
        s.add(Application(job_id=applied_job, status=ApplicationStatus.applied))
        prof = s.get(SearchProfile, p)
        prof.required_tech = ["rust"]  # now neither posting qualifies
        s.add(prof)
        s.commit()
    stats = matching.match_profile(p, rematch=True)
    assert stats.kept_acted == 1
    assert _verdicts(fresh_db, p) == {
        applied_job: FilterStatus.passed,  # you applied: it stays in your queue
        other_job: FilterStatus.dropped,
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


def test_title_preskip_only_skips_a_title_every_profile_rejects():
    senior = build_config(role_titles=[], seniority_terms=["senior"], required_tech=["ts"], excluded_tech=[])
    manager = build_config(role_titles=[], seniority_terms=["manager"], required_tech=["ts"], excluded_tech=[])
    union = union_title_config([senior, manager])
    assert not title_quick_fail("Engineering Manager", union)
    assert not title_quick_fail("Senior Engineer", union)
    assert title_quick_fail("Junior Engineer", union)
    # Sales titles are a shared rule: skipped regardless.
    assert title_quick_fail("Senior Account Executive", union)
