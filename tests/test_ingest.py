from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from job_applier.ingest import STALE_AFTER_DAYS, IngestStats, _is_stale, ingest_one
from job_applier.models import JobPosting
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
