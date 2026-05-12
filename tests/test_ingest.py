from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from job_applier.ingest import (
    PRUNE_INGESTED_AFTER_DAYS,
    PRUNE_POSTED_AFTER_DAYS,
    STALE_AFTER_DAYS,
    IngestStats,
    _is_stale,
    archive_existing_duplicates,
    cross_source_hash,
    ingest_one,
    prune_old_postings,
)
from job_applier.models import Application, ApplicationStatus, Company, JobPosting
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


def _insert_posting(
    session,
    *,
    source_id: str,
    company: Company,
    posted_at=None,
    ingested_at=None,
    description: str = "lots of text",
    raw: dict | None = None,
) -> JobPosting:
    p = JobPosting(
        source="test",
        source_id=source_id,
        url=f"https://example.com/{source_id}",
        title=f"Senior Engineer {source_id}",
        description=description,
        dedupe_hash=f"h-{source_id}",
        company_id=company.id,
        posted_at=posted_at,
        raw=raw if raw is not None else {"body": "x" * 50},
    )
    session.add(p)
    session.flush()
    if ingested_at is not None:
        p.ingested_at = ingested_at
        session.add(p)
        session.flush()
    return p


class TestPruneOldPostings:
    def _company(self, session) -> Company:
        c = Company(name="Acme")
        session.add(c)
        session.flush()
        return c

    def test_archived_application_is_lightened(self, session):
        c = self._company(session)
        now = datetime.now(timezone.utc)
        p = _insert_posting(
            session, source_id="t-1", company=c, posted_at=now, ingested_at=now
        )
        session.add(Application(job_id=p.id, status=ApplicationStatus.archived))
        session.commit()

        stats = prune_old_postings(session, now=now)
        assert stats.lightened == 1
        assert stats.bytes_freed > 0
        session.refresh(p)
        assert p.description == ""
        assert p.raw == {}
        # Dedupe inputs preserved
        assert p.dedupe_hash == "h-t-1"
        assert p.title == "Senior Engineer t-1"

    def test_rejected_application_is_lightened(self, session):
        c = self._company(session)
        now = datetime.now(timezone.utc)
        p = _insert_posting(
            session, source_id="t-r", company=c, posted_at=now, ingested_at=now
        )
        session.add(Application(job_id=p.id, status=ApplicationStatus.rejected))
        session.commit()

        prune_old_postings(session, now=now)
        session.refresh(p)
        assert p.description == ""

    def test_old_posted_at_is_lightened(self, session):
        c = self._company(session)
        now = datetime.now(timezone.utc)
        old_posted = now - timedelta(days=PRUNE_POSTED_AFTER_DAYS + 1)
        p = _insert_posting(
            session, source_id="t-old", company=c, posted_at=old_posted, ingested_at=now
        )
        session.commit()

        stats = prune_old_postings(session, now=now)
        assert stats.lightened == 1
        session.refresh(p)
        assert p.description == ""

    def test_old_ingested_without_applied_is_lightened(self, session):
        c = self._company(session)
        now = datetime.now(timezone.utc)
        old_ingested = now - timedelta(days=PRUNE_INGESTED_AFTER_DAYS + 1)
        p = _insert_posting(
            session, source_id="t-ing", company=c, posted_at=now, ingested_at=old_ingested
        )
        session.commit()

        prune_old_postings(session, now=now)
        session.refresh(p)
        assert p.description == ""

    def test_old_ingested_with_applied_is_kept(self, session):
        c = self._company(session)
        now = datetime.now(timezone.utc)
        old_ingested = now - timedelta(days=PRUNE_INGESTED_AFTER_DAYS + 1)
        p = _insert_posting(
            session, source_id="t-app", company=c, posted_at=now, ingested_at=old_ingested
        )
        session.add(Application(job_id=p.id, status=ApplicationStatus.applied))
        session.commit()

        stats = prune_old_postings(session, now=now)
        assert stats.lightened == 0
        session.refresh(p)
        assert p.description != ""

    def test_fresh_posting_is_kept(self, session):
        c = self._company(session)
        now = datetime.now(timezone.utc)
        p = _insert_posting(
            session, source_id="t-fresh", company=c, posted_at=now, ingested_at=now
        )
        session.commit()

        stats = prune_old_postings(session, now=now)
        assert stats.lightened == 0
        session.refresh(p)
        assert p.description != ""

    def test_already_lightened_is_skipped(self, session):
        c = self._company(session)
        now = datetime.now(timezone.utc)
        _insert_posting(
            session,
            source_id="t-empty",
            company=c,
            posted_at=now - timedelta(days=PRUNE_POSTED_AFTER_DAYS + 1),
            ingested_at=now,
            description="",
            raw={},
        )
        session.commit()

        stats = prune_old_postings(session, now=now)
        assert stats.lightened == 0

    def test_dedupe_after_prune_still_works(self, session):
        c = self._company(session)
        now = datetime.now(timezone.utc)
        # Pre-load a posting that will get pruned (old posted_at)
        old = now - timedelta(days=PRUNE_POSTED_AFTER_DAYS + 1)
        session.add(
            JobPosting(
                source="test",
                source_id="t-1",
                url="https://example.com/1",
                title="Senior Software Engineer",
                description="big description",
                dedupe_hash="h-t-1",
                company_id=c.id,
                posted_at=old,
                ingested_at=now,
                raw={"body": "x" * 100},
            )
        )
        session.commit()
        prune_old_postings(session, now=now)

        # Now an ingest of a different source_id but same source + company + title
        # should be caught by the existing-duplicate check, which relies on title
        # being intact.
        stats = IngestStats()
        ingest_one(
            session,
            _raw(source_id="t-2", title="Senior Software Engineer"),
            stats,
        )
        session.commit()
        assert stats.skipped_duplicate == 1
        assert stats.inserted == 0


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
