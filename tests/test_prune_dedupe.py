"""Prune must not destroy the input the JD-SimHash layer needs.

``prune_old_postings`` blanks ``description``; ``dedupe_jd_backfill`` derives
``jd_fingerprint`` *from* ``description``; and ``ingest_one`` is INSERT-only, so
a blanked description never comes back. Running ``make prune`` before
``make dedupe-jd`` therefore used to drop every pruned posting out of the
near-duplicate layer permanently — silently, because ``jd_simhash("")`` returns
None and the backfill just skips those rows. The fix fingerprints each row on
its way out of prune; these tests pin that, plus the surrounding promise that
prune only ever lightens ``description``/``raw`` and never touches a dedupe
column or deletes a row.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from job_applier.dedupe import jd_simhash
from job_applier.maintenance import (
    PRUNE_POSTED_AFTER_DAYS,
    dedupe_jd_backfill,
    prune_old_postings,
)
from job_applier.models import Company, JobPosting

_LONG_JD = (
    "We are looking for a senior software engineer to lead our backend platform. "
    "You will build distributed services in TypeScript and Node.js, work closely "
    "with product, and mentor mid-level engineers. Our stack runs on Kubernetes "
    "with PostgreSQL and Redis. We value clean code, async-friendly patterns, "
    "good observability, and a bias toward simple, boring infrastructure. "
    "Remote-first; we offer competitive comp, equity, and generous PTO. "
    "Apply if you've shipped real systems and enjoy mentoring."
)


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


@pytest.fixture
def now() -> datetime:
    return datetime.now(timezone.utc)


def _company(session, name: str) -> Company:
    c = Company(name=name)
    session.add(c)
    session.flush()
    return c


def _posting(session, *, source_id: str, company: Company, posted_at, **overrides):
    fields = dict(
        source="greenhouse",
        source_id=source_id,
        url=f"https://example.com/{source_id}",
        title="Senior Backend Engineer",
        description=_LONG_JD,
        dedupe_hash=f"h-{source_id}",
        company_id=company.id,
        posted_at=posted_at,
        raw={"body": "x" * 50},
    )
    ingested_at = overrides.pop("ingested_at", None)
    fields.update(overrides)
    p = JobPosting(**fields)
    session.add(p)
    session.flush()
    if ingested_at is not None:
        p.ingested_at = ingested_at
        session.add(p)
        session.flush()
    return p


class TestPrunePreservesFingerprintability:
    """The `make prune` -> `make dedupe-jd` ordering must not cost the user the
    JD near-duplicate layer."""

    def test_pruned_row_is_fingerprinted_on_its_way_out(self, session, now):
        c = _company(session, "Acme")
        old = now - timedelta(days=PRUNE_POSTED_AFTER_DAYS + 1)
        p = _posting(session, source_id="t-1", company=c, posted_at=old, ingested_at=now)
        session.commit()
        assert p.jd_fingerprint is None  # nothing has fingerprinted it yet

        stats = prune_old_postings(session, now=now)

        assert stats.lightened == 1
        session.refresh(p)
        assert p.description == ""  # still lightened
        assert p.jd_fingerprint == jd_simhash(_LONG_JD)

    def test_prune_before_backfill_still_links_near_duplicates(self, session, now):
        # The concrete regression: the user runs `make prune`, then `make dedupe-jd`.
        # Both postings carry the same JD from different employers/sources, so the
        # backfill should soft-link the later one to the earlier.
        old = now - timedelta(days=PRUNE_POSTED_AFTER_DAYS + 1)
        earlier = _posting(
            session,
            source_id="t-early",
            company=_company(session, "Acme"),
            posted_at=old,
            ingested_at=now - timedelta(days=2),
        )
        later = _posting(
            session,
            source_id="t-late",
            company=_company(session, "Acme Staffing"),
            source="remoteok",
            posted_at=old,
            ingested_at=now,
        )
        session.commit()

        prune_old_postings(session, now=now)
        stats = dedupe_jd_backfill(session=session)

        # Prune already did the hashing, so the backfill has nothing left to
        # fingerprint — but it can still cluster, which is the whole point.
        assert stats.fingerprinted == 0
        assert stats.flagged == 1
        session.refresh(earlier)
        session.refresh(later)
        assert earlier.jd_fingerprint is not None
        assert later.duplicate_of == earlier.id

    def test_pruned_row_matches_an_unpruned_near_duplicate(self, session, now):
        # Mixed corpus: an old (pruned) posting and a fresh one that keeps its
        # description. The pruned row must still be a clustering candidate.
        earlier = _posting(
            session,
            source_id="t-old",
            company=_company(session, "Acme"),
            posted_at=now - timedelta(days=PRUNE_POSTED_AFTER_DAYS + 1),
            ingested_at=now - timedelta(days=2),
        )
        later = _posting(
            session,
            source_id="t-new",
            company=_company(session, "Acme Staffing"),
            source="remoteok",
            posted_at=now,
            ingested_at=now,
        )
        session.commit()

        stats = prune_old_postings(session, now=now)
        assert stats.lightened == 1  # only the old one

        dedupe_jd_backfill(session=session)
        session.refresh(earlier)
        session.refresh(later)
        assert earlier.description == ""
        assert later.description == _LONG_JD
        assert later.duplicate_of == earlier.id

    def test_existing_fingerprint_is_not_recomputed(self, session, now):
        # An already-fingerprinted row keeps its stored value verbatim; prune is
        # a backstop for un-fingerprinted rows, not a re-hasher.
        c = _company(session, "Acme")
        p = _posting(
            session,
            source_id="t-fp",
            company=c,
            posted_at=now - timedelta(days=PRUNE_POSTED_AFTER_DAYS + 1),
            ingested_at=now,
            jd_fingerprint="deadbeefdeadbeef",
        )
        session.commit()

        prune_old_postings(session, now=now)

        session.refresh(p)
        assert p.jd_fingerprint == "deadbeefdeadbeef"

    def test_thin_description_stays_unfingerprinted(self, session, now):
        # Below JD_MIN_CHARS jd_simhash returns None; prune must record that as
        # NULL rather than inventing a collision-prone hash.
        c = _company(session, "Acme")
        p = _posting(
            session,
            source_id="t-thin",
            company=c,
            posted_at=now - timedelta(days=PRUNE_POSTED_AFTER_DAYS + 1),
            ingested_at=now,
            description="too short to fingerprint",
        )
        session.commit()

        prune_old_postings(session, now=now)

        session.refresh(p)
        assert p.description == ""
        assert p.jd_fingerprint is None

    def test_untouched_row_is_not_fingerprinted(self, session, now):
        # Prune only hashes rows it is about to lighten — a posting that doesn't
        # meet the criteria is left exactly as it was.
        c = _company(session, "Acme")
        p = _posting(session, source_id="t-fresh", company=c, posted_at=now, ingested_at=now)
        session.commit()

        stats = prune_old_postings(session, now=now)

        assert stats.lightened == 0
        session.refresh(p)
        assert p.description == _LONG_JD
        assert p.jd_fingerprint is None


class TestPrunePreservesDedupeColumns:
    """The module docstring promises the dedupe columns survive prune."""

    @pytest.mark.parametrize(
        "column,value",
        [
            ("dedupe_hash", "h-t-keep"),
            ("cross_source_hash", "c" * 64),
            ("jd_fingerprint", "0123456789abcdef"),
            ("duplicate_of", None),  # set to a real id in the test body
        ],
    )
    def test_dedupe_column_survives_prune(self, session, now, column, value):
        c = _company(session, "Acme")
        canonical = _posting(
            session, source_id="t-canon", company=c, posted_at=now, ingested_at=now
        )
        session.commit()
        if column == "duplicate_of":
            value = canonical.id

        p = _posting(
            session,
            source_id="t-keep",
            company=c,
            posted_at=now - timedelta(days=PRUNE_POSTED_AFTER_DAYS + 1),
            ingested_at=now,
            **{column: value},
        )
        session.commit()

        stats = prune_old_postings(session, now=now)

        assert stats.lightened == 1
        session.refresh(p)
        assert getattr(p, column) == value

    @pytest.mark.parametrize("column", ["source", "source_id", "url", "title", "location"])
    def test_normalized_title_inputs_survive_prune(self, session, now, column):
        c = _company(session, "Acme")
        p = _posting(
            session,
            source_id="t-inputs",
            company=c,
            posted_at=now - timedelta(days=PRUNE_POSTED_AFTER_DAYS + 1),
            ingested_at=now,
            location="Remote - US",
        )
        session.commit()
        before = getattr(p, column)

        prune_old_postings(session, now=now)

        session.refresh(p)
        assert getattr(p, column) == before
        assert p.company_id == c.id

    def test_prune_never_deletes_rows(self, session, now):
        c = _company(session, "Acme")
        old = now - timedelta(days=PRUNE_POSTED_AFTER_DAYS + 1)
        for i in range(3):
            _posting(session, source_id=f"t-{i}", company=c, posted_at=old, ingested_at=now)
        session.commit()

        stats = prune_old_postings(session, now=now)

        assert stats.lightened == 3
        assert len(session.exec(select(JobPosting)).all()) == 3
