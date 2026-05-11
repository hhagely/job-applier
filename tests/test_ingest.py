from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from job_applier.ingest import (
    STALE_AFTER_DAYS,
    IngestStats,
    _is_stale,
    archive_existing_duplicates,
    cross_source_hash,
    ingest_one,
)
from job_applier.models import Application, ApplicationStatus, JobPosting
from job_applier.sources.base import RawJob


@pytest.fixture
def session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _raw(**overrides) -> RawJob:
    defaults = dict(
        source="test",
        source_id="t-1",
        url="https://example.com/jobs/1",
        title="Senior Software Engineer",
        company_name="Acme",
        description="We use TypeScript and React on Node.js.",
        location="Remote — US",
        remote=True,
    )
    defaults.update(overrides)
    return RawJob(**defaults)


class TestIsStale:
    def test_none_posted_at_is_not_stale(self):
        now = datetime(2026, 5, 8, tzinfo=timezone.utc)
        assert _is_stale(None, now) is False

    def test_recent_aware_is_not_stale(self):
        now = datetime(2026, 5, 8, tzinfo=timezone.utc)
        assert _is_stale(now - timedelta(days=10), now) is False

    def test_old_aware_is_stale(self):
        now = datetime(2026, 5, 8, tzinfo=timezone.utc)
        assert _is_stale(now - timedelta(days=STALE_AFTER_DAYS + 1), now) is True

    def test_naive_datetime_treated_as_utc(self):
        now = datetime(2026, 5, 8, tzinfo=timezone.utc)
        naive_old = (now - timedelta(days=STALE_AFTER_DAYS + 1)).replace(tzinfo=None)
        assert _is_stale(naive_old, now) is True


class TestIngestOneStaleness:
    def test_stale_posting_is_skipped(self, session):
        old = datetime.now(timezone.utc) - timedelta(days=STALE_AFTER_DAYS + 5)
        stats = IngestStats()
        ingest_one(session, _raw(posted_at=old), stats)
        session.commit()

        assert stats.stale == 1
        assert stats.inserted == 0
        assert session.exec(select(JobPosting)).first() is None

    def test_recent_posting_is_persisted(self, session):
        recent = datetime.now(timezone.utc) - timedelta(days=2)
        stats = IngestStats()
        ingest_one(session, _raw(posted_at=recent), stats)
        session.commit()

        assert stats.stale == 0
        assert stats.inserted == 1
        assert session.exec(select(JobPosting)).first() is not None

    def test_unknown_posted_at_is_persisted(self, session):
        stats = IngestStats()
        ingest_one(session, _raw(posted_at=None), stats)
        session.commit()

        assert stats.stale == 0
        assert stats.inserted == 1


class TestContentDedupe:
    def test_same_title_different_source_id_is_skipped(self, session):
        stats = IngestStats()
        ingest_one(session, _raw(source_id="t-sf", location="San Francisco"), stats)
        ingest_one(session, _raw(source_id="t-ny", location="New York"), stats)
        session.commit()

        assert stats.inserted == 1
        assert stats.skipped_duplicate == 1
        assert len(session.exec(select(JobPosting)).all()) == 1

    def test_title_match_is_case_and_whitespace_insensitive(self, session):
        stats = IngestStats()
        ingest_one(session, _raw(source_id="t-1", title="Senior Software Engineer"), stats)
        ingest_one(
            session,
            _raw(source_id="t-2", title="  senior   software   engineer  "),
            stats,
        )
        session.commit()

        assert stats.inserted == 1
        assert stats.skipped_duplicate == 1

    def test_different_title_same_company_is_kept(self, session):
        stats = IngestStats()
        ingest_one(session, _raw(source_id="t-1", title="Senior Software Engineer"), stats)
        ingest_one(session, _raw(source_id="t-2", title="Staff Software Engineer"), stats)
        session.commit()

        assert stats.inserted == 2
        assert stats.skipped_duplicate == 0

    def test_same_title_different_company_is_kept(self, session):
        stats = IngestStats()
        ingest_one(session, _raw(source_id="t-1", company_name="Acme"), stats)
        ingest_one(session, _raw(source_id="t-2", company_name="Globex"), stats)
        session.commit()

        assert stats.inserted == 2
        assert stats.skipped_duplicate == 0


class TestArchiveExistingDuplicates:
    def test_archives_later_postings_in_a_dupe_group(self, session):
        # Bypass ingest_one's dedupe by inserting directly to simulate pre-fix state.
        from job_applier.models import Company

        c = Company(name="Acme")
        session.add(c)
        session.flush()
        for i, src_id in enumerate(["t-1", "t-2", "t-3"]):
            session.add(
                JobPosting(
                    source="test",
                    source_id=src_id,
                    url=f"https://example.com/{src_id}",
                    title="Senior Software Engineer",
                    description="x",
                    dedupe_hash=f"h-{i}",
                    company_id=c.id,
                )
            )
        session.commit()

        archived = archive_existing_duplicates(session)
        assert archived == 2

        apps = session.exec(select(Application)).all()
        statuses = {a.job_id: a.status for a in apps}
        kept = session.exec(
            select(JobPosting).where(JobPosting.source_id == "t-1")
        ).first()
        assert kept is not None
        assert kept.id not in statuses  # earliest is untouched
        assert all(s == ApplicationStatus.archived for s in statuses.values())


class TestCrossSourceHash:
    def test_same_company_and_title_collide_across_sources(self):
        a = _raw(source="greenhouse", source_id="gh-1", company_name="Stripe Inc")
        b = _raw(source="ashby", source_id="ash-1", company_name="Stripe, Inc.")
        assert cross_source_hash(a) == cross_source_hash(b)

    def test_seniority_abbreviations_collide(self):
        a = _raw(title="Senior Software Engineer")
        b = _raw(title="Sr. Software Engineer")
        assert cross_source_hash(a) == cross_source_hash(b)

    def test_remote_qualifier_in_title_collides(self):
        a = _raw(title="Senior Software Engineer")
        b = _raw(title="Senior Software Engineer (Remote)")
        c = _raw(title="Senior Software Engineer - US")
        assert cross_source_hash(a) == cross_source_hash(b) == cross_source_hash(c)

    def test_different_titles_do_not_collide(self):
        a = _raw(title="Senior Software Engineer")
        b = _raw(title="Senior Frontend Engineer")
        assert cross_source_hash(a) != cross_source_hash(b)

    def test_different_companies_do_not_collide(self):
        a = _raw(company_name="Stripe")
        b = _raw(company_name="Square")
        assert cross_source_hash(a) != cross_source_hash(b)

    def test_thin_input_returns_none(self):
        # Single-letter company / very short title => no fingerprint
        assert cross_source_hash(_raw(company_name="X", title="Eng")) is None
        assert cross_source_hash(_raw(company_name="", title="Senior Engineer")) is None


class TestIngestCrossSourceDedupe:
    def test_second_source_for_same_role_is_skipped(self, session):
        stats = IngestStats()
        first = _raw(source="greenhouse", source_id="gh-99", company_name="Acme Inc")
        second = _raw(source="ashby", source_id="ash-99", company_name="Acme, Inc.")
        ingest_one(session, first, stats)
        ingest_one(session, second, stats)
        session.commit()

        assert stats.inserted == 1
        assert stats.skipped_cross_source == 1
        assert len(session.exec(select(JobPosting)).all()) == 1

    def test_thin_fingerprint_does_not_block_inserts(self, session):
        # Both rows would get NULL cross_source_hash — they should not collide
        stats = IngestStats()
        a = _raw(source="hn", source_id="hn-1", company_name="X", title="Eng")
        b = _raw(source="hn", source_id="hn-2", company_name="Y", title="Dev")
        ingest_one(session, a, stats)
        ingest_one(session, b, stats)
        session.commit()
        # Both pass the dedupe (different per-source hashes); neither has a
        # cross-source hash to collide on. Filter may or may not pass them — we
        # only check that the cross-source path didn't double-block.
        assert stats.skipped_cross_source == 0
