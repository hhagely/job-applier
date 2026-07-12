from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from job_applier.ingest import (
    JD_HAMMING_THRESHOLD,
    PRUNE_INGESTED_AFTER_DAYS,
    PRUNE_POSTED_AFTER_DAYS,
    STALE_AFTER_DAYS,
    IngestStats,
    _is_stale,
    archive_existing_duplicates,
    backfill_cross_source_hash,
    cross_source_hash,
    dedupe_jd_backfill,
    ingest_one,
    jd_hamming_distance,
    jd_simhash,
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


# A real-shaped JD long enough to clear the JD_MIN_CHARS floor. Same description
# under different titles/companies is what JD-similarity dedupe needs to catch.
_LONG_JD = (
    "We are looking for a senior software engineer to lead our backend platform. "
    "You will build distributed services in TypeScript and Node.js, work closely "
    "with product, and mentor mid-level engineers. Our stack runs on Kubernetes "
    "with PostgreSQL and Redis. We value clean code, async-friendly patterns, "
    "good observability, and a bias toward simple, boring infrastructure. "
    "Remote-first; we offer competitive comp, equity, and generous PTO. "
    "Apply if you've shipped real systems and enjoy mentoring."
)


class TestJdSimhash:
    def test_long_description_produces_hex_fingerprint(self):
        fp = jd_simhash(_LONG_JD)
        assert fp is not None
        assert len(fp) == 16
        assert int(fp, 16) >= 0

    def test_short_description_returns_none(self):
        assert jd_simhash("too short") is None
        assert jd_simhash("") is None

    def test_html_is_stripped(self):
        plain = _LONG_JD
        wrapped = f"<p>{_LONG_JD}</p>"
        assert jd_simhash(plain) == jd_simhash(wrapped)

    def test_identical_text_collides(self):
        assert jd_simhash(_LONG_JD) == jd_simhash(_LONG_JD)

    def test_minor_edit_is_within_threshold(self):
        a = jd_simhash(_LONG_JD)
        b = jd_simhash(_LONG_JD + " We also love good documentation.")
        assert a is not None and b is not None
        assert jd_hamming_distance(a, b) <= JD_HAMMING_THRESHOLD

    def test_very_different_text_exceeds_threshold(self):
        other = (
            "Marketing manager needed for our consumer goods company. "
            "Five years of brand management, agency relationships, and "
            "campaign analytics. Strong copywriting required. Reports to "
            "the VP of Marketing. Hybrid Chicago office, four days per week."
        ) * 2
        a = jd_simhash(_LONG_JD)
        b = jd_simhash(other)
        assert a is not None and b is not None
        assert jd_hamming_distance(a, b) > JD_HAMMING_THRESHOLD


class TestIngestJdSimilarity:
    def test_same_jd_across_sources_is_flagged(self, session):
        stats = IngestStats()
        # First post from Greenhouse — company name matches so cross-source-hash
        # would fire; use different company names to force the JD path.
        first = _raw(
            source="greenhouse",
            source_id="gh-1",
            company_name="Acme",
            title="Senior Backend Engineer",
            description=_LONG_JD,
        )
        # Second post from RemoteOK reblasted under an aggregator-style company
        # name and a slightly reworded title. Different cross-source hash, but
        # the JD is the same.
        second = _raw(
            source="remoteok",
            source_id="rok-1",
            company_name="Acme Staffing",
            title="Senior Software Engineer (Backend)",
            description=_LONG_JD,
        )
        ingest_one(session, first, stats)
        ingest_one(session, second, stats)
        session.commit()

        assert stats.inserted == 2
        assert stats.flagged_jd_similar == 1
        rows = session.exec(
            select(JobPosting).order_by(JobPosting.id)
        ).all()
        assert rows[0].duplicate_of is None
        assert rows[1].duplicate_of == rows[0].id
        assert rows[0].jd_fingerprint is not None
        assert rows[0].jd_fingerprint == rows[1].jd_fingerprint

    def test_unrelated_descriptions_are_not_flagged(self, session):
        stats = IngestStats()
        a = _raw(
            source="greenhouse",
            source_id="gh-a",
            company_name="Acme",
            title="Senior Backend Engineer",
            description=_LONG_JD,
        )
        b = _raw(
            source="lever",
            source_id="lv-b",
            company_name="Globex",
            title="Senior Frontend Engineer",
            description=(
                "Globex needs a senior frontend engineer to build our customer "
                "portal in React. Five years experience with TypeScript, SSR, "
                "and design systems. Tight collaboration with design and product. "
                "Strong CSS chops required; accessibility background a plus. "
                "Remote in North America, occasional travel for offsites."
            ),
        )
        ingest_one(session, a, stats)
        ingest_one(session, b, stats)
        session.commit()

        assert stats.flagged_jd_similar == 0
        rows = session.exec(select(JobPosting).order_by(JobPosting.id)).all()
        assert all(r.duplicate_of is None for r in rows)


class TestDedupeJdBackfill:
    def _seed(self, session, ingested_at_offsets: list[timedelta]):
        c1 = Company(name="Acme")
        c2 = Company(name="Acme Staffing")
        session.add(c1)
        session.add(c2)
        session.flush()
        now = datetime.now(timezone.utc)
        postings: list[JobPosting] = []
        for i, offset in enumerate(ingested_at_offsets):
            p = JobPosting(
                source="greenhouse" if i == 0 else "remoteok",
                source_id=f"src-{i}",
                url=f"https://example.com/{i}",
                title="Senior Backend Engineer",
                description=_LONG_JD,
                dedupe_hash=f"h-{i}",
                company_id=(c1.id if i == 0 else c2.id),
                ingested_at=now + offset,
            )
            session.add(p)
            postings.append(p)
        session.commit()
        return postings

    def test_backfill_fingerprints_and_links_recent_duplicates(self, session):
        earlier, later = self._seed(
            session, [timedelta(days=-2), timedelta(0)]
        )

        stats = dedupe_jd_backfill(session=session)

        assert stats.fingerprinted == 2
        assert stats.flagged == 1
        session.refresh(earlier)
        session.refresh(later)
        assert earlier.duplicate_of is None
        assert later.duplicate_of == earlier.id
        assert earlier.jd_fingerprint is not None

    def test_backfill_respects_cluster_window(self, session):
        # 60 days apart > 30-day window → should NOT be linked even though the
        # JDs are identical.
        earlier, later = self._seed(
            session, [timedelta(days=-60), timedelta(0)]
        )

        stats = dedupe_jd_backfill(session=session)

        assert stats.fingerprinted == 2
        assert stats.flagged == 0
        session.refresh(later)
        assert later.duplicate_of is None

    def test_backfill_is_idempotent(self, session):
        self._seed(session, [timedelta(days=-2), timedelta(0)])
        first = dedupe_jd_backfill(session=session)
        second = dedupe_jd_backfill(session=session)

        assert first.flagged == 1
        # Second pass: fingerprints already populated, links already set.
        assert second.fingerprinted == 0
        assert second.flagged == 0


class TestCrossSourceHashBackfill:
    """`backfill_cross_source_hash` opens its own session via ``engine()``, so we
    point that at a throwaway in-memory DB rather than using the ``session``
    fixture directly. It lives in ``maintenance`` (re-exported from ``ingest``),
    so patch the engine there."""

    def _fresh_engine(self, monkeypatch):
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        SQLModel.metadata.create_all(engine)
        monkeypatch.setattr("job_applier.maintenance.engine", lambda: engine)
        return engine

    def _add(self, session, company_id, *, source, source_id, title):
        session.add(
            JobPosting(
                source=source,
                source_id=source_id,
                url=f"https://example.com/{source_id}",
                title=title,
                description="",
                dedupe_hash=f"h-{source_id}",
                company_id=company_id,
            )
        )

    def test_backfills_and_skips_intra_run_duplicates(self, monkeypatch):
        engine = self._fresh_engine(monkeypatch)
        with Session(engine) as s:
            c = Company(name="Acme")
            s.add(c)
            s.flush()
            # dup-0 / dup-1 share company + title -> same cross-source key.
            self._add(s, c.id, source="greenhouse", source_id="dup-0", title="Senior Backend Engineer")
            self._add(s, c.id, source="lever", source_id="dup-1", title="Senior Backend Engineer")
            self._add(s, c.id, source="ashby", source_id="solo", title="Staff Frontend Engineer")
            s.commit()

        # dup-0 + solo get a hash; dup-1 collides with dup-0 and is left NULL so
        # the original ingest isn't retroactively flagged as the duplicate.
        assert backfill_cross_source_hash() == 2

        with Session(engine) as s:
            by_id = {r.source_id: r for r in s.exec(select(JobPosting)).all()}
            assert by_id["dup-0"].cross_source_hash is not None
            assert by_id["dup-1"].cross_source_hash is None
            assert by_id["solo"].cross_source_hash is not None

    def test_idempotent_once_all_rows_hashed(self, monkeypatch):
        engine = self._fresh_engine(monkeypatch)
        with Session(engine) as s:
            c = Company(name="Acme")
            s.add(c)
            s.flush()
            self._add(s, c.id, source="greenhouse", source_id="a", title="Senior Backend Engineer")
            self._add(s, c.id, source="lever", source_id="b", title="Staff Frontend Engineer")
            s.commit()

        assert backfill_cross_source_hash() == 2
        # No NULL-hash rows remain, so a second pass is a no-op.
        assert backfill_cross_source_hash() == 0
