"""Ingestion pipeline: pull raw jobs from sources, dedupe, filter, persist.

The fingerprint/normalization primitives live in :mod:`job_applier.dedupe` and the
offline batch jobs (prune, backfills) in :mod:`job_applier.maintenance`; both are
re-exported here so existing ``from job_applier.ingest import ...`` call sites keep
working. This module owns only the live pipeline — ``ingest_one`` / ``run_ingest``
— plus the post-ingest ``archive_existing_duplicates`` reconciliation.
"""

from __future__ import annotations

import copy
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlmodel import Session, select

from job_applier.dedupe import (
    JD_HAMMING_THRESHOLD,
    cross_source_hash,
    dedupe_hash,
    jd_hamming_distance,
    jd_simhash,
    normalize_company,
    normalize_title,
)
from job_applier.filters import FilterConfig, evaluate, load_active_config
from job_applier.maintenance import (
    PRUNE_INGESTED_AFTER_DAYS,
    PRUNE_POSTED_AFTER_DAYS,
    backfill_cross_source_hash,
    dedupe_jd_backfill,
    prune_old_postings,
)
from job_applier.models import (
    Application,
    ApplicationStatus,
    BlacklistedCompany,
    Company,
    JobPosting,
    engine,
)
from job_applier.models.db import FilterStatus
from job_applier.sources import RawJob, SourceAdapter, get_all_sources

log = logging.getLogger(__name__)

# Re-exported for backward compatibility with `from job_applier.ingest import X`.
__all__ = [
    "JD_HAMMING_THRESHOLD",
    "JD_LOOKBACK_DAYS",
    "PRUNE_INGESTED_AFTER_DAYS",
    "PRUNE_POSTED_AFTER_DAYS",
    "STALE_AFTER_DAYS",
    "IngestStats",
    "archive_existing_duplicates",
    "backfill_cross_source_hash",
    "cross_source_hash",
    "dedupe_hash",
    "dedupe_jd_backfill",
    "ingest_one",
    "jd_hamming_distance",
    "jd_simhash",
    "load_blacklisted_names",
    "normalize_company",
    "normalize_title",
    "prune_old_postings",
    "run_ingest",
]

# Postings older than this are skipped — stale listings are rarely still open,
# and a re-post will come through on the next ingest if the role is real.
STALE_AFTER_DAYS = 30

# How far back to look for near-duplicates at ingest. Reposts arrive within a
# few weeks; older matches add cost without catching much.
JD_LOOKBACK_DAYS = 14


@dataclass
class IngestStats:
    fetched: int = 0
    inserted: int = 0
    skipped_duplicate: int = 0
    skipped_cross_source: int = 0
    passed_filter: int = 0
    dropped_filter: int = 0
    dropped_blacklist: int = 0
    manual_review: int = 0
    stale: int = 0
    flagged_jd_similar: int = 0


def _is_stale(posted_at: datetime | None, now: datetime) -> bool:
    if posted_at is None:
        return False
    if posted_at.tzinfo is None:
        posted_at = posted_at.replace(tzinfo=timezone.utc)
    return (now - posted_at) > timedelta(days=STALE_AFTER_DAYS)


def _upsert_company(session: Session, name: str) -> Company:
    company = session.exec(select(Company).where(Company.name == name)).first()
    if company is None:
        company = Company(name=name)
        session.add(company)
        session.flush()
    return company


def load_blacklisted_names(session: Session) -> frozenset[str]:
    """Normalized names of every user-blacklisted company.

    Loaded once per ingest run and handed to ``ingest_one`` so the per-job check
    is an O(1) set lookup rather than a DB query per posting.
    """
    rows = session.exec(select(BlacklistedCompany.normalized_name)).all()
    return frozenset(rows)


def ingest_one(
    session: Session,
    raw: RawJob,
    stats: IngestStats,
    *,
    filter_config: FilterConfig | None = None,
    blacklist: frozenset[str] | None = None,
) -> None:
    stats.fetched += 1

    # User company blacklist: drop before any other work so a blacklisted
    # employer never lands in the queue, even the first time we see them (no
    # Company row need exist yet). Matches on the same normalized key as
    # cross-source dedupe, so naming variants collapse.
    if blacklist and normalize_company(raw.company_name) in blacklist:
        stats.dropped_blacklist += 1
        return

    h = dedupe_hash(raw)

    existing = session.exec(select(JobPosting).where(JobPosting.dedupe_hash == h)).first()
    if existing is not None:
        stats.skipped_duplicate += 1
        return

    if _is_stale(raw.posted_at, datetime.now(timezone.utc)):
        stats.stale += 1
        return

    decision = evaluate(raw, filter_config)
    if decision.status == FilterStatus.dropped:
        stats.dropped_filter += 1
        return

    company = _upsert_company(session, raw.company_name)
    if company.is_blocked:
        stats.dropped_filter += 1
        return

    # Some employers post the same role under one source_id per city. Treat any
    # existing posting from the same source + company with the same normalized
    # title as a duplicate so we don't flood the queue.
    norm = normalize_title(raw.title, raw.location)
    existing_dup = session.exec(
        select(JobPosting).where(
            JobPosting.source == raw.source,
            JobPosting.company_id == company.id,
        )
    ).all()
    if any(normalize_title(p.title, p.location) == norm for p in existing_dup):
        stats.skipped_duplicate += 1
        return

    cross_h = cross_source_hash(raw)
    if cross_h is not None:
        cross_match = session.exec(
            select(JobPosting).where(JobPosting.cross_source_hash == cross_h)
        ).first()
        if cross_match is not None:
            stats.skipped_cross_source += 1
            return

    jd_fp = jd_simhash(raw.description)
    duplicate_of: int | None = None
    if jd_fp is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=JD_LOOKBACK_DAYS)
        recent = session.exec(
            select(JobPosting)
            .where(JobPosting.jd_fingerprint.is_not(None))  # type: ignore[union-attr]
            .where(JobPosting.ingested_at >= cutoff)
        ).all()
        for candidate in recent:
            if candidate.jd_fingerprint is None:
                continue
            if jd_hamming_distance(jd_fp, candidate.jd_fingerprint) <= JD_HAMMING_THRESHOLD:
                # First-ingested wins — chase the canonical chain so we don't
                # link to a row that's itself flagged as a dup.
                duplicate_of = candidate.duplicate_of or candidate.id
                break

    posting = JobPosting(
        source=raw.source,
        source_id=raw.source_id,
        url=raw.url,
        title=raw.title,
        description=raw.description,
        location=raw.location,
        remote=raw.remote,
        employment_type=raw.employment_type,
        posted_at=raw.posted_at,
        dedupe_hash=h,
        cross_source_hash=cross_h,
        jd_fingerprint=jd_fp,
        duplicate_of=duplicate_of,
        raw=raw.raw,
        company_id=company.id,
    )
    if duplicate_of is not None:
        stats.flagged_jd_similar += 1

    posting.filter_status = decision.status
    posting.filter_reason = decision.reason
    if decision.status == FilterStatus.passed:
        stats.passed_filter += 1
    else:
        stats.manual_review += 1

    session.add(posting)
    stats.inserted += 1


def run_ingest(
    sources: list[SourceAdapter] | None = None,
    progress_cb: Callable[[int, int, str, IngestStats], None] | None = None,
) -> IngestStats:
    """Fetch, dedupe, filter, and persist from every source.

    ``progress_cb(done, total, source_name, cumulative_stats)`` is invoked after
    each source finishes (optional; when omitted, behavior is identical to before).

    One shared session, committed per source: a single source raising (a bad
    payload, a network blip) is logged and skipped so it can't discard every other
    source's rows. Cross-source dedupe is unaffected — the shared session's
    autoflush makes already-ingested rows visible to later sources regardless of
    commit timing. On failure the partial source's uncommitted rows are rolled back
    and its stats are restored so the summary stays truthful.
    """
    stats = IngestStats()
    with Session(engine()) as session:
        filter_config = load_active_config(session)
        blacklist = load_blacklisted_names(session)
        if sources is None:
            sources = get_all_sources(filter_config=filter_config)
        total = len(sources)
        for i, source in enumerate(sources):
            snapshot = copy.copy(stats)
            try:
                for raw in source.fetch():
                    ingest_one(
                        session,
                        raw,
                        stats,
                        filter_config=filter_config,
                        blacklist=blacklist,
                    )
                session.commit()
            except Exception as exc:  # noqa: BLE001 - one source can't abort the run
                session.rollback()
                stats.__dict__.update(snapshot.__dict__)
                log.warning("source %s failed during ingest, skipping: %s", source.name, exc)
            if progress_cb is not None:
                progress_cb(i + 1, total, source.name, stats)
    return stats


def archive_existing_duplicates(session: Session) -> int:
    """Archive postings that share (source, company, normalized-title) with
    another posting in the same group. Keeps the earliest *non-archived*
    posting as the canonical (so a previously-archived city variant doesn't
    end up swallowing every sibling). Returns the number of postings archived.
    """
    postings = session.exec(select(JobPosting).order_by(JobPosting.id)).all()
    archived_job_ids = {
        a.job_id
        for a in session.exec(
            select(Application).where(Application.status == ApplicationStatus.archived)
        ).all()
    }
    groups: dict[tuple[str, int | None, str], list[JobPosting]] = {}
    for p in postings:
        key = (p.source, p.company_id, normalize_title(p.title, p.location))
        groups.setdefault(key, []).append(p)

    archived = 0
    for ps in groups.values():
        if len(ps) < 2:
            continue
        keeper = next((p for p in ps if p.id not in archived_job_ids), ps[0])
        for p in ps:
            if p.id == keeper.id:
                continue
            app = session.exec(select(Application).where(Application.job_id == p.id)).first()
            if app is None:
                session.add(Application(job_id=p.id, status=ApplicationStatus.archived))
            elif app.status != ApplicationStatus.archived:
                app.status = ApplicationStatus.archived
                session.add(app)
            else:
                continue
            archived += 1
    session.commit()
    return archived
