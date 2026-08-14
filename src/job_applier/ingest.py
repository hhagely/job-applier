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


def _upsert_company(session: Session, name: str, caches: "_IngestCaches") -> tuple[int, bool]:
    """Resolve ``name`` to ``(company_id, is_blocked)``, inserting on first sight.

    Reads through ``caches.companies``, so the common case (a company we have seen
    before) costs no query at all.
    """
    entry = caches.companies.get(name)
    if entry is None:
        company = Company(name=name)
        session.add(company)
        session.flush()  # assign the PK so postings in this batch can reference it
        entry = (company.id, company.is_blocked)
        caches.companies[name] = entry
    return entry


def load_blacklisted_names(session: Session) -> frozenset[str]:
    """Normalized names of every user-blacklisted company.

    Loaded once per ingest run and handed to ``ingest_one`` so the per-job check
    is an O(1) set lookup rather than a DB query per posting.
    """
    rows = session.exec(select(BlacklistedCompany.normalized_name)).all()
    return frozenset(rows)


# How many raw jobs to accumulate from a source before opening a write
# transaction to persist them. Bounding this is half of what keeps a scrape from
# freezing the rest of the app; see ``run_ingest``.
INGEST_BATCH_SIZE = 100


@dataclass
class _IngestCaches:
    """In-memory mirrors of the dedupe lookups ``ingest_one`` would otherwise run
    as a fresh query per raw job.

    Built once per run and updated in place as rows are inserted, which reduces
    the write transaction to (near enough) pure INSERTs. That matters for more
    than speed: the transaction holds SQLite's single write lock, and for as long
    as it is open every *other* writer in the app — a status change from the
    queue, a note, a follow-up date — blocks on ``busy_timeout`` and then fails
    with "database is locked".

    The JD scan is the reason this exists rather than being a nice-to-have: it
    used to load every fingerprinted posting from the lookback window *per
    incoming job*, so the cost of a batch grew with the product of the two.
    """

    # JobPosting.dedupe_hash for every posting.
    hashes: set[str]
    # Non-null JobPosting.cross_source_hash for every posting.
    cross: set[str]
    # (source, company_id, normalized title) for every posting.
    titles: set[tuple[str, int, str]]
    # (canonical posting id, fingerprint) for postings recent enough to match a
    # near-duplicate JD against. Canonical means the row's ``duplicate_of`` when
    # it is itself a dup, so a later match never links to a link.
    jd: list[tuple[int, str]]
    # company name -> (id, is_blocked)
    companies: dict[str, tuple[int, bool]]

    @classmethod
    def load(cls, session: Session, *, now: datetime | None = None) -> "_IngestCaches":
        """Snapshot the dedupe state from the DB. Four queries, once per run."""
        cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=JD_LOOKBACK_DAYS)
        rows = session.exec(
            select(
                JobPosting.source,
                JobPosting.company_id,
                JobPosting.title,
                JobPosting.location,
                JobPosting.dedupe_hash,
                JobPosting.cross_source_hash,
            )
        ).all()
        jd_rows = session.exec(
            select(JobPosting.id, JobPosting.duplicate_of, JobPosting.jd_fingerprint)
            .where(JobPosting.jd_fingerprint.is_not(None))  # type: ignore[union-attr]
            .where(JobPosting.ingested_at >= cutoff)
        ).all()
        company_rows = session.exec(
            select(Company.id, Company.name, Company.is_blocked)
        ).all()
        return cls(
            hashes={r[4] for r in rows},
            cross={r[5] for r in rows if r[5] is not None},
            titles={
                (r[0], r[1], normalize_title(r[2], r[3])) for r in rows if r[1] is not None
            },
            jd=[(dup_of or pid, fp) for pid, dup_of, fp in jd_rows],
            companies={name: (cid, blocked) for cid, name, blocked in company_rows},
        )


def ingest_one(
    session: Session,
    raw: RawJob,
    stats: IngestStats,
    *,
    filter_config: FilterConfig | None = None,
    blacklist: frozenset[str] | None = None,
    caches: "_IngestCaches | None" = None,
) -> None:
    """Dedupe, filter, and (if it survives) persist one raw job into ``session``.

    ``caches`` carries the dedupe state across calls; ``run_ingest`` builds it
    once per run. When omitted it is loaded from ``session`` on every call, which
    keeps this callable standalone at the cost of a reload per job — fine for the
    handful of rows a test or a one-off script pushes through, not for a real run.
    """
    if caches is None:
        caches = _IngestCaches.load(session)

    stats.fetched += 1

    # User company blacklist: drop before any other work so a blacklisted
    # employer never lands in the queue, even the first time we see them (no
    # Company row need exist yet). Matches on the same normalized key as
    # cross-source dedupe, so naming variants collapse.
    if blacklist and normalize_company(raw.company_name) in blacklist:
        stats.dropped_blacklist += 1
        return

    h = dedupe_hash(raw)

    if h in caches.hashes:
        stats.skipped_duplicate += 1
        return

    if _is_stale(raw.posted_at, datetime.now(timezone.utc)):
        stats.stale += 1
        return

    decision = evaluate(raw, filter_config)
    if decision.status == FilterStatus.dropped:
        stats.dropped_filter += 1
        return

    company_id, is_blocked = _upsert_company(session, raw.company_name, caches)
    if is_blocked:
        stats.dropped_filter += 1
        return

    # Some employers post the same role under one source_id per city. Treat any
    # existing posting from the same source + company with the same normalized
    # title as a duplicate so we don't flood the queue.
    title_key = (raw.source, company_id, normalize_title(raw.title, raw.location))
    if title_key in caches.titles:
        stats.skipped_duplicate += 1
        return

    cross_h = cross_source_hash(raw)
    if cross_h is not None and cross_h in caches.cross:
        stats.skipped_cross_source += 1
        return

    jd_fp = jd_simhash(raw.description)
    duplicate_of: int | None = None
    if jd_fp is not None:
        for candidate_id, candidate_fp in caches.jd:
            if jd_hamming_distance(jd_fp, candidate_fp) <= JD_HAMMING_THRESHOLD:
                # First-ingested wins, and the cache already stores the canonical
                # id, so we never link to a row that's itself flagged as a dup.
                duplicate_of = candidate_id
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
        company_id=company_id,
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
    if jd_fp is not None and duplicate_of is None:
        # Flush to get the assigned PK: this row becomes the canonical target for
        # any later near-duplicate JD, so the cache needs a real id to link to.
        session.flush()

    # Keep the caches level with the session, so rows added earlier in this run
    # dedupe against rows added later exactly as they would have via a re-query.
    caches.hashes.add(h)
    if cross_h is not None:
        caches.cross.add(cross_h)
    caches.titles.add(title_key)
    if jd_fp is not None:
        caches.jd.append((duplicate_of or posting.id, jd_fp))

    stats.inserted += 1


def _batched(items, size: int):
    """Yield ``items`` in lists of at most ``size``.

    If the underlying iterable raises part-way (a source generator hitting a bad
    payload mid-sweep), the partly-filled batch is discarded along with the
    exception — it never reaches a session, matching the "a failed source's
    unwritten rows are dropped" contract.
    """
    batch = []
    for item in items:
        batch.append(item)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def _write_batch(
    batch: list[RawJob],
    stats: IngestStats,
    *,
    filter_config: FilterConfig | None,
    blacklist: frozenset[str] | None,
    caches: _IngestCaches,
) -> None:
    """Persist one batch in its own short-lived session + transaction.

    Rolls the batch's stats back alongside its rows if the write fails, then
    re-raises so the caller can abandon the source.
    """
    snapshot = copy.copy(stats)
    with Session(engine()) as session:
        try:
            for raw in batch:
                ingest_one(
                    session,
                    raw,
                    stats,
                    filter_config=filter_config,
                    blacklist=blacklist,
                    caches=caches,
                )
            session.commit()
        except Exception:
            session.rollback()
            stats.__dict__.update(snapshot.__dict__)
            raise


def run_ingest(
    sources: list[SourceAdapter] | None = None,
    progress_cb: Callable[[int, int, str, IngestStats], None] | None = None,
    *,
    batch_size: int = INGEST_BATCH_SIZE,
) -> IngestStats:
    """Fetch, dedupe, filter, and persist from every source.

    ``progress_cb(done, total, source_name, cumulative_stats)`` is invoked after
    each source finishes (optional).

    **No DB transaction is ever held across network I/O.** Adapters fetch lazily —
    one HTTP request per company slug — so writing as we consume the generator
    would pin SQLite's single write lock for the entire multi-minute sweep of a
    several-hundred-slug board, and every status change the user made meanwhile
    would block on ``busy_timeout`` and then fail with "database is locked".
    Instead each source is drained ``batch_size`` jobs at a time with no session
    open, and each batch is written in its own short transaction. Reads were never
    affected (WAL lets them run alongside a writer), which is why the app stayed
    navigable while mutations did not.

    Failure isolation is per batch rather than per source: a source raising is
    logged and skipped so it can't abort the run, but the batches it already
    committed are kept — a network blip 400 jobs into a board no longer throws
    those 400 away. Only the in-flight batch's rows and stats are dropped.

    Cross-source dedupe is unaffected: batches commit before the next one reads,
    so later sources see earlier rows the same way the old shared session's
    autoflush made them visible.
    """
    stats = IngestStats()
    with Session(engine()) as session:
        filter_config = load_active_config(session)
        blacklist = load_blacklisted_names(session)
        if sources is None:
            sources = get_all_sources(filter_config=filter_config)
        caches = _IngestCaches.load(session)

    total = len(sources)
    for i, source in enumerate(sources):
        try:
            for batch in _batched(source.fetch(), batch_size):
                _write_batch(
                    batch,
                    stats,
                    filter_config=filter_config,
                    blacklist=blacklist,
                    caches=caches,
                )
        except Exception as exc:  # noqa: BLE001 - one source can't abort the run
            log.warning("source %s failed during ingest, skipping: %s", source.name, exc)
            # A rolled-back batch leaves the caches holding rows that were never
            # committed, which would make the next source skip real jobs as
            # duplicates. Reload from what actually landed.
            with Session(engine()) as session:
                caches = _IngestCaches.load(session)
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
