"""Ingestion pipeline: pull raw jobs from sources, dedupe, persist, filter."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlmodel import Session, select

from job_applier.filters import evaluate
from job_applier.models import Application, ApplicationStatus, Company, JobPosting, engine
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
    skipped_cross_source: int = 0
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


_LOCATION_SUFFIX_FALLBACK = re.compile(
    r"\s*[\-–—]\s*"
    r"[^,\-–—]{1,40},\s*[^,\-–—]{1,40}"
    r"(?:,\s*[^,\-–—]{1,40})?"
    r"\s*$"
)


def _strip_location_suffix(title: str, location: str | None) -> str:
    """Remove a trailing " - {location}" from a title.

    Speechify and other Greenhouse posters fan a single role out into one
    posting per city; the title gets " - {City}, {State}, {Country}" appended
    and the `location` field carries that same suffix verbatim. Stripping it
    lets dedupe see the underlying role.
    """
    if location:
        pattern = re.compile(
            r"\s*[\-–—]\s*" + re.escape(location.strip()) + r"\s*$",
            re.IGNORECASE,
        )
        stripped = pattern.sub("", title)
        if stripped != title:
            return stripped
    return _LOCATION_SUFFIX_FALLBACK.sub("", title)


def normalize_title(title: str, location: str | None = None) -> str:
    return " ".join(_strip_location_suffix(title, location).lower().split())


_COMPANY_SUFFIXES = re.compile(
    r"[,\s]+(inc|incorporated|llc|ltd|limited|corp|corporation|co|company|"
    r"plc|gmbh|s\.?a\.?|s\.?l\.?|pty|ag|nv|bv)\.?$",
    re.IGNORECASE,
)
_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_TITLE_NOISE = re.compile(
    r"\b(remote|us|usa|united\s+states|anywhere|global|emea|apac|americas|"
    r"\(remote\)|\(us\)|\(usa\)|m/?f/?d|f/?m/?d|h/?f|m/?w/?d)\b",
    re.IGNORECASE,
)


def _normalize_company(name: str) -> str:
    s = (name or "").strip()
    s = _COMPANY_SUFFIXES.sub("", s)
    s = _NON_ALNUM.sub("", s.lower())
    return s


def _normalize_title(title: str, location: str | None = None) -> str:
    s = _strip_location_suffix(title or "", location).lower()
    s = re.sub(r"\bsr\.?\b", "senior", s)
    s = re.sub(r"\bjr\.?\b", "junior", s)
    s = re.sub(r"\beng\.?\b", "engineer", s)
    s = re.sub(r"\bdev\.?\b", "developer", s)
    s = _TITLE_NOISE.sub("", s)
    s = _NON_ALNUM.sub("", s)
    return s


def cross_source_hash(raw: RawJob) -> str | None:
    """Hash a normalized (company, title) so the same role from different
    sources collapses to one row. Returns None if either field is too thin
    to fingerprint reliably (avoids false-positive collisions)."""
    company = _normalize_company(raw.company_name)
    title = _normalize_title(raw.title, raw.location)
    if len(company) < 2 or len(title) < 4:
        return None
    h = hashlib.sha256()
    h.update(company.encode())
    h.update(b"|")
    h.update(title.encode())
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
