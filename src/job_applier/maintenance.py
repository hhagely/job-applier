"""Offline maintenance jobs: prune heavy fields, backfill dedupe columns.

Batch operations run from the CLI (``prune`` / ``dedupe-jd`` / ``init``), not on
the hot ingest path. Split out of ``ingest`` so the live pipeline stays focused;
the fingerprint/normalization primitives they lean on live in ``dedupe``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlmodel import Session, select

from job_applier.contracts import RawJob
from job_applier.dedupe import (
    JD_HAMMING_THRESHOLD,
    cross_source_hash,
    jd_hamming_distance,
    jd_simhash,
)
from job_applier.models import Application, ApplicationStatus, JobPosting, engine

# Postings that match prune criteria have their description + raw blob cleared
# to keep the DB small. The dedupe columns (source/source_id/dedupe_hash/
# cross_source_hash) and the normalized-title inputs (title/location/company)
# stay intact so future ingests still see these as duplicates.
PRUNE_POSTED_AFTER_DAYS = 30
PRUNE_INGESTED_AFTER_DAYS = 14


@dataclass
class PruneStats:
    scanned: int = 0
    lightened: int = 0
    bytes_freed: int = 0


def prune_old_postings(session: Session, now: datetime | None = None) -> PruneStats:
    """Null out heavy fields (description, raw) on postings we no longer need
    in full. Targets:

    - archived or rejected applications
    - postings whose posted_at is over PRUNE_POSTED_AFTER_DAYS old
    - postings ingested over PRUNE_INGESTED_AFTER_DAYS ago that haven't been
      applied to

    Dedupe still works against these rows because the hash columns and the
    normalized-title inputs are untouched.
    """
    now = now or datetime.now(timezone.utc)
    posted_cutoff = now - timedelta(days=PRUNE_POSTED_AFTER_DAYS)
    ingested_cutoff = now - timedelta(days=PRUNE_INGESTED_AFTER_DAYS)

    app_status_by_job: dict[int, ApplicationStatus] = {
        a.job_id: a.status for a in session.exec(select(Application)).all()
    }

    stats = PruneStats()
    postings = session.exec(select(JobPosting)).all()
    for p in postings:
        stats.scanned += 1
        if not p.description and not p.raw:
            continue

        status = app_status_by_job.get(p.id) if p.id is not None else None
        prune = False
        if status in (ApplicationStatus.archived, ApplicationStatus.rejected):
            prune = True
        else:
            posted = p.posted_at
            if posted is not None and posted.tzinfo is None:
                posted = posted.replace(tzinfo=timezone.utc)
            if posted is not None and posted < posted_cutoff:
                prune = True
            else:
                ingested = p.ingested_at
                if ingested is not None and ingested.tzinfo is None:
                    ingested = ingested.replace(tzinfo=timezone.utc)
                if (
                    ingested is not None
                    and ingested < ingested_cutoff
                    and status != ApplicationStatus.applied
                ):
                    prune = True

        if not prune:
            continue

        stats.bytes_freed += len(p.description or "")
        if p.raw:
            try:
                stats.bytes_freed += len(json.dumps(p.raw))
            except (TypeError, ValueError):
                pass
        p.description = ""
        p.raw = {}
        session.add(p)
        stats.lightened += 1

    if stats.lightened:
        session.commit()
    return stats


@dataclass
class JdDedupeStats:
    fingerprinted: int = 0
    flagged: int = 0


# Clustering window for the backfill CLI. Wider than the per-ingest lookback
# because we're sweeping historical data, not making latency-sensitive
# decisions.
JD_CLUSTER_WINDOW_DAYS = 30


def dedupe_jd_backfill(session: Session | None = None) -> JdDedupeStats:
    """Populate jd_fingerprint on legacy rows and soft-link near-duplicates.

    Clustering rule: for each row missing duplicate_of, find earlier rows
    within JD_CLUSTER_WINDOW_DAYS whose fingerprint is within
    JD_HAMMING_THRESHOLD bits. First match wins; the later row gets the link.

    Passing an explicit ``session`` makes the helper unit-testable; otherwise
    it opens its own session against the global engine.
    """
    if session is None:
        with Session(engine()) as s:
            return _dedupe_jd_backfill(s)
    return _dedupe_jd_backfill(session)


def _dedupe_jd_backfill(session: Session) -> JdDedupeStats:
    stats = JdDedupeStats()
    rows = session.exec(select(JobPosting).order_by(JobPosting.ingested_at)).all()

    for row in rows:
        if row.jd_fingerprint is not None:
            continue
        fp = jd_simhash(row.description or "")
        if fp is None:
            continue
        row.jd_fingerprint = fp
        session.add(row)
        stats.fingerprinted += 1
    if stats.fingerprinted:
        session.commit()

    rows = session.exec(
        select(JobPosting)
        .where(JobPosting.jd_fingerprint.is_not(None))  # type: ignore[union-attr]
        .order_by(JobPosting.ingested_at)
    ).all()

    window = timedelta(days=JD_CLUSTER_WINDOW_DAYS)
    for i, later in enumerate(rows):
        if later.duplicate_of is not None:
            continue
        later_ingested = later.ingested_at
        if later_ingested is not None and later_ingested.tzinfo is None:
            later_ingested = later_ingested.replace(tzinfo=timezone.utc)
        for earlier in rows[:i]:
            if earlier.id == later.id:
                continue
            earlier_ingested = earlier.ingested_at
            if earlier_ingested is not None and earlier_ingested.tzinfo is None:
                earlier_ingested = earlier_ingested.replace(tzinfo=timezone.utc)
            if (
                later_ingested is not None
                and earlier_ingested is not None
                and (later_ingested - earlier_ingested) > window
            ):
                continue
            if (
                jd_hamming_distance(
                    later.jd_fingerprint,  # type: ignore[arg-type]
                    earlier.jd_fingerprint,  # type: ignore[arg-type]
                )
                <= JD_HAMMING_THRESHOLD
            ):
                later.duplicate_of = earlier.duplicate_of or earlier.id
                session.add(later)
                stats.flagged += 1
                break
    if stats.flagged:
        session.commit()
    return stats


def backfill_cross_source_hash() -> int:
    """Populate cross_source_hash on existing rows that pre-date the column.

    Rows with the same fingerprint as an earlier row are left with NULL hash
    (so we don't punish the original ingest by retroactively flagging it as a
    dup). Returns the number of rows updated.
    """
    updated = 0
    seen: set[str] = set()
    with Session(engine()) as session:
        rows = session.exec(
            select(JobPosting).where(JobPosting.cross_source_hash.is_(None))  # type: ignore[union-attr]
        ).all()
        for row in rows:
            company = row.company.name if row.company else ""
            fake = RawJob(
                source=row.source,
                source_id=row.source_id,
                url=row.url,
                title=row.title,
                company_name=company,
                description="",
                location=row.location,
            )
            h = cross_source_hash(fake)
            if h is None or h in seen:
                continue
            seen.add(h)
            row.cross_source_hash = h
            session.add(row)
            updated += 1
        if updated:
            session.commit()
    return updated
