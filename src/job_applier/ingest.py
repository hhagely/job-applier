"""Ingestion pipeline: pull raw jobs from sources, dedupe, persist, filter."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlmodel import Session, select

from job_applier.filters import evaluate
from job_applier.models import Company, JobPosting, engine
from job_applier.models.db import FilterStatus
from job_applier.sources import RawJob, SourceAdapter, get_all_sources

# Postings older than this are skipped — stale listings are rarely still open,
# and a re-post will come through on the next ingest if the role is real.
STALE_AFTER_DAYS = 30


@dataclass
class IngestStats:
    fetched: int = 0
    inserted: int = 0
    skipped_duplicate: int = 0
    passed_filter: int = 0
    dropped_filter: int = 0
    manual_review: int = 0
    stale: int = 0


def _is_stale(posted_at: datetime | None, now: datetime) -> bool:
    if posted_at is None:
        return False
    if posted_at.tzinfo is None:
        posted_at = posted_at.replace(tzinfo=timezone.utc)
    return (now - posted_at) > timedelta(days=STALE_AFTER_DAYS)


def dedupe_hash(raw: RawJob) -> str:
    h = hashlib.sha256()
    h.update(raw.source.encode())
    h.update(b"|")
    h.update(raw.source_id.encode())
    return h.hexdigest()


def _upsert_company(session: Session, name: str) -> Company:
    company = session.exec(select(Company).where(Company.name == name)).first()
    if company is None:
        company = Company(name=name)
        session.add(company)
        session.flush()
    return company


def ingest_one(session: Session, raw: RawJob, stats: IngestStats) -> None:
    stats.fetched += 1
    h = dedupe_hash(raw)

    existing = session.exec(select(JobPosting).where(JobPosting.dedupe_hash == h)).first()
    if existing is not None:
        stats.skipped_duplicate += 1
        return

    if _is_stale(raw.posted_at, datetime.now(timezone.utc)):
        stats.stale += 1
        return

    decision = evaluate(raw)
    if decision.status == FilterStatus.dropped:
        stats.dropped_filter += 1
        return

    company = _upsert_company(session, raw.company_name)
    if company.is_blocked:
        stats.dropped_filter += 1
        return

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
        raw=raw.raw,
        company_id=company.id,
    )

    posting.filter_status = decision.status
    posting.filter_reason = decision.reason
    if decision.status == FilterStatus.passed:
        stats.passed_filter += 1
    else:
        stats.manual_review += 1

    session.add(posting)
    stats.inserted += 1


def run_ingest(sources: list[SourceAdapter] | None = None) -> IngestStats:
    if sources is None:
        sources = get_all_sources()
    stats = IngestStats()
    with Session(engine()) as session:
        for source in sources:
            for raw in source.fetch():
                ingest_one(session, raw, stats)
        session.commit()
    return stats
