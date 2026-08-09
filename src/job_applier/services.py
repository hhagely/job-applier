"""Shared persistence/query logic reused by both the HTTP routes and the AI
orchestrator (Phase 4). Keeping the score upsert, pending-match selection, and
bulk-status mutation here means there is exactly one code path for each — the
background scorer and the REST endpoints can't drift.

These functions take an explicit ``Session``, accept primitives, and return ORM
rows (or raise plain exceptions ``JobNotFound`` / ``ValueError``); the HTTP layer
owns the request/response DTOs and maps those exceptions to status codes. Keeping
this module free of ``job_applier.api`` imports is deliberate: the application
layer must not depend on the web edge, so a background thread (or a second entry
point) can call it without dragging in FastAPI.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional

from sqlalchemy import or_
from sqlalchemy.orm import selectinload
from sqlmodel import Session, select

from job_applier.config import settings
from job_applier.ingest import normalize_company
from job_applier.models.db import (
    Application,
    ApplicationStatus,
    BlacklistedCompany,
    Company,
    FilterStatus,
    JobPosting,
    MatchScore,
    MatchScoreHistory,
    Resume,
    SearchProfile,
    SourceSlug,
)
from job_applier.sources import discover


class JobNotFound(Exception):
    """Raised when a job id doesn't resolve to a row."""

    def __init__(self, job_id: int) -> None:
        super().__init__(f"job {job_id} not found")
        self.job_id = job_id


def active_resume(session: Session) -> Optional[Resume]:
    return session.exec(
        select(Resume).where(Resume.is_active == True)  # noqa: E712
    ).first()


# ---- scoring persistence --------------------------------------------------


def upsert_score(
    session: Session,
    job_id: int,
    *,
    score: int,
    rubric: Optional[dict] = None,
    reasoning: Optional[str] = None,
    scored_by: str = "claude-code",
    score_kind: str = "baseline",
) -> MatchScore:
    """Upsert the active score for a job, snapshotting the prior value to history.

    One code path for the REST endpoint and the background scorer. Baseline
    scores are stamped with the active resume id (so they can go stale); tailored
    scores carry no resume id by design.
    """
    job = session.get(JobPosting, job_id)
    if job is None:
        raise JobNotFound(job_id)
    if not 0 <= score <= 100:
        raise ValueError("score must be 0-100")

    existing = job.score
    if existing is not None:
        session.add(
            MatchScoreHistory(
                job_id=existing.job_id,
                score=existing.score,
                rubric=existing.rubric,
                reasoning=existing.reasoning,
                scored_by=existing.scored_by,
                scored_at=existing.scored_at,
                resume_id=existing.resume_id,
                score_kind=existing.score_kind,
            )
        )

    resume = active_resume(session)
    row = existing or MatchScore(job_id=job_id)
    row.score = score
    row.rubric = rubric or {}
    row.reasoning = reasoning
    row.scored_by = scored_by
    row.scored_at = datetime.now(timezone.utc)
    row.score_kind = score_kind
    row.resume_id = resume.id if resume and score_kind == "baseline" else None
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def adopt_scores(session: Session, *, resume_id: int) -> int:
    """Re-stamp baseline scores onto ``resume_id`` so they stop reading as stale.

    The escape hatch for a *minor* resume edit. Staleness is only an id mismatch
    (see ``score_out``), and every upload writes a new ``Resume`` row, so fixing a
    typo would otherwise invalidate every score at once and force a full re-run.
    Adopting re-points the active rows instead — no AI calls, one UPDATE.

    Only active ``MatchScore`` rows move; ``MatchScoreHistory`` keeps the resume
    each score was really computed against, so the audit trail stays honest. The
    caller owns the judgment that the edit was small enough to keep the numbers.

    Returns the number of scores adopted.
    """
    rows = session.exec(
        select(MatchScore).where(
            MatchScore.resume_id.is_not(None),  # type: ignore[union-attr]
            MatchScore.resume_id != resume_id,
        )
    ).all()
    for row in rows:
        row.resume_id = resume_id
        session.add(row)
    session.commit()
    return len(rows)


# ---- pending-match selection ----------------------------------------------


def select_pending_jobs(
    session: Session, *, limit: int = 25, include_stale: bool = False
) -> list[JobPosting]:
    """Jobs that passed the hard filter and need scoring.

    Always includes unscored jobs. With ``include_stale``, also includes jobs
    whose only score is against a non-active resume.
    """
    # Eager-load the relationships the selection + its consumers read per row
    # (score for _needs_scoring; company/application for the pending-match
    # serializer and the scoring loop), avoiding a lazy load per job.
    stmt = (
        select(JobPosting)
        .where(JobPosting.filter_status == FilterStatus.passed)
        .options(
            selectinload(JobPosting.company),
            selectinload(JobPosting.score),
            selectinload(JobPosting.application),
        )
        .order_by(JobPosting.ingested_at.desc())
    )
    jobs = list(session.exec(stmt).all())
    active_id = active_resume(session).id if include_stale and active_resume(session) else None

    def _needs_scoring(j: JobPosting) -> bool:
        if j.score is None:
            return True
        if include_stale and active_id is not None:
            sid = j.score.resume_id
            return sid is not None and sid != active_id
        return False

    return [j for j in jobs if _needs_scoring(j)][:limit]


# ---- bulk status ----------------------------------------------------------


def apply_status_transition(
    app_row: Application,
    *,
    new_status: ApplicationStatus,
    now: datetime,
    next_followup_at: Optional[datetime] = None,
    last_contact_at: Optional[datetime] = None,
    outcome: Optional[str] = None,
) -> None:
    """Mutate ``app_row`` for a status change, defaulting the follow-up date when
    transitioning into ``applied``."""
    app_row.status = new_status
    if new_status == ApplicationStatus.applied and app_row.applied_at is None:
        app_row.applied_at = now
    if next_followup_at is not None:
        app_row.next_followup_at = next_followup_at
    elif (
        new_status == ApplicationStatus.applied
        and app_row.next_followup_at is None
        and app_row.applied_at is not None
    ):
        app_row.next_followup_at = app_row.applied_at + timedelta(
            days=settings.followup_default_days
        )
    if last_contact_at is not None:
        app_row.last_contact_at = last_contact_at
    if outcome is not None:
        app_row.outcome = outcome
    app_row.updated_at = now


def bulk_set_status(
    session: Session,
    job_ids: list[int],
    status: ApplicationStatus,
    *,
    next_followup_at: Optional[datetime] = None,
    last_contact_at: Optional[datetime] = None,
    outcome: Optional[str] = None,
) -> list[Application]:
    """Set status on many jobs in one commit. Raises ``JobNotFound`` on any bad id."""
    now = datetime.now(timezone.utc)
    results: list[Application] = []
    for job_id in job_ids:
        job = session.get(JobPosting, job_id)
        if job is None:
            raise JobNotFound(job_id)
        app_row = job.application or Application(job_id=job_id)
        apply_status_transition(
            app_row,
            new_status=status,
            now=now,
            next_followup_at=next_followup_at,
            last_contact_at=last_contact_at,
            outcome=outcome,
        )
        session.add(app_row)
        results.append(app_row)
    session.commit()
    return results


# ---- posting search -------------------------------------------------------

#: Below this a substring match is too noisy to be useful (and "a" would scan
#: the whole table for nothing).
SEARCH_MIN_TERM = 2


def _like_contains(term: str) -> str:
    """A LIKE pattern matching ``term`` anywhere, with wildcards escaped so a
    query like "50%" or "back_end" is taken literally."""
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def search_jobs(session: Session, query: str, *, limit: int = 20) -> list[JobPosting]:
    """Ingested postings whose title or company name contains ``query``.

    Deliberately wider than the queue view: it spans every persisted posting
    (passed *and* manual, archived included) because the point is to find a job
    you know was ingested, not to browse the current queue. Hidden duplicates are
    skipped so one role doesn't fill the list with its cross-source twins.

    Results are ranked exact match -> prefix match -> substring; the SQL ordering
    is recency and Python's sort is stable, so newest wins inside each band.
    """
    term = query.strip()
    if len(term) < SEARCH_MIN_TERM:
        return []
    pattern = _like_contains(term)
    stmt = (
        select(JobPosting)
        .join(Company, isouter=True)
        .where(
            JobPosting.duplicate_of.is_(None),  # type: ignore[union-attr]
            or_(
                JobPosting.title.ilike(pattern, escape="\\"),  # type: ignore[attr-defined]
                Company.name.ilike(pattern, escape="\\"),  # type: ignore[attr-defined]
            ),
        )
        .options(
            selectinload(JobPosting.company),
            selectinload(JobPosting.score),
            selectinload(JobPosting.application),
        )
        .order_by(JobPosting.ingested_at.desc())  # type: ignore[union-attr]
    )
    jobs = list(session.exec(stmt).all())

    needle = term.lower()

    def rank(j: JobPosting) -> int:
        fields = [j.title.lower(), (j.company.name if j.company else "").lower()]
        if any(f == needle for f in fields):
            return 0
        if any(f.startswith(needle) for f in fields):
            return 1
        return 2

    jobs.sort(key=rank)
    return jobs[:limit]


# ---- search profile -------------------------------------------------------


def load_or_create_profile(session: Session) -> SearchProfile:
    p = session.exec(select(SearchProfile).order_by(SearchProfile.id)).first()
    if p is None:
        p = SearchProfile()
        session.add(p)
        session.flush()
    return p


def save_recommendations(session: Session, recommendations: dict) -> SearchProfile:
    """Persist an LLM proposal as a draft on the profile. Never mutates the active
    fields — the user reviews + accepts via PUT to apply. ``recommendations`` is a
    plain dict (the router/flow owns the DTO it was validated from)."""
    p = load_or_create_profile(session)
    p.recommendations_draft = dict(recommendations)
    p.updated_at = datetime.now(timezone.utc)
    session.add(p)
    session.commit()
    session.refresh(p)
    return p


# ---- company blacklist ----------------------------------------------------


class BlacklistNameTooShort(ValueError):
    """A company name that normalizes to fewer than 2 alphanumeric chars — too
    thin to match on reliably at ingest, so we refuse to store it."""


def list_blacklisted_companies(session: Session) -> list[BlacklistedCompany]:
    """Every blacklisted company, ordered case-insensitively by name."""
    return list(
        session.exec(
            select(BlacklistedCompany).order_by(BlacklistedCompany.normalized_name)
        ).all()
    )


def add_blacklisted_company(
    session: Session, name: str, reason: Optional[str] = None
) -> BlacklistedCompany:
    """Add a company to the ingest blacklist. Idempotent on the normalized name.

    Returns the existing row if the company is already blacklisted (under any
    naming variant) so re-adding is a no-op rather than a unique-constraint
    error. Raises ``BlacklistNameTooShort`` when the name is too thin to match.
    """
    display = (name or "").strip()
    normalized = normalize_company(display)
    if len(normalized) < 2:
        raise BlacklistNameTooShort(
            "enter a company name with at least two letters or digits"
        )
    existing = session.exec(
        select(BlacklistedCompany).where(
            BlacklistedCompany.normalized_name == normalized
        )
    ).first()
    if existing is not None:
        return existing
    row = BlacklistedCompany(
        name=display,
        normalized_name=normalized,
        reason=(reason or "").strip() or None,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def remove_blacklisted_company(session: Session, blacklist_id: int) -> bool:
    """Remove a blacklist entry by id. Returns True if a row was deleted."""
    row = session.get(BlacklistedCompany, blacklist_id)
    if row is None:
        return False
    session.delete(row)
    session.commit()
    return True


# ---- company whitelist (hand-added boards) --------------------------------
#
# The mirror image of the blacklist: employers the user specifically wants
# searched, added one at a time when feed discovery hasn't picked them up.
# There's no separate table — an added company is a ``SourceSlug`` row flagged
# ``added_by_user``, so ingest picks it up with no special-casing and the
# company-coverage count includes it.


class WatchedCompanyError(ValueError):
    """Base for add-a-company failures the UI should show as a message."""


class WatchedCompanyBlacklisted(WatchedCompanyError):
    """The company is on the ingest blacklist, so watching it would be a no-op."""


class WatchedCompanyUnknownUrl(WatchedCompanyError):
    """A URL was pasted but it isn't a job board we know how to read."""


class WatchedCompanyNotFound(WatchedCompanyError):
    """No live board could be found for the typed company name."""


class WatchedCompanyUnreachable(WatchedCompanyError):
    """A board URL parsed, but the board itself didn't respond."""


@dataclass
class AddWatchedResult:
    """Outcome of an add attempt. ``already_searched`` is the "we're already
    watching this one" notice — a normal result, not an error."""

    status: Literal["added", "already_searched"]
    message: str
    companies: list[SourceSlug]


def list_watched_companies(session: Session) -> list[SourceSlug]:
    """Boards the user added by hand, newest first — the whitelist as shown at
    ``/search``. Excludes the thousands that arrived via seed/feed discovery."""
    return list(
        session.exec(
            select(SourceSlug)
            .where(SourceSlug.added_by_user == True)  # noqa: E712
            .order_by(SourceSlug.added_at.desc())  # type: ignore[union-attr]
        ).all()
    )


def company_display_name(row: SourceSlug) -> str:
    """What to call a watched board in the UI: the name the user typed, else the
    slug (for packed Workday/Oracle slugs, just the tenant)."""
    return row.label or row.slug.split("|")[0]


def _slug_company_key(slug: str) -> str:
    """The company-identifying part of a slug, normalized for comparison.
    Packed slugs (Workday ``tenant|region|site``) key on the tenant."""
    return re.sub(r"[^a-z0-9]", "", slug.split("|")[0].lower())


def find_watched_board(session: Session, source: str, slug: str) -> Optional[SourceSlug]:
    """The existing row for an exact ``(source, slug)`` pair, if any — however it
    got there (seed, discovery, or a previous manual add)."""
    return session.exec(
        select(SourceSlug).where(SourceSlug.source == source, SourceSlug.slug == slug)
    ).first()


def find_watched_by_name(session: Session, name: str) -> Optional[SourceSlug]:
    """The first already-watched board whose slug matches a company name.

    This is the "you're already searching this company" check. It's a name-to-slug
    comparison rather than anything authoritative — slugs are all we store — so it
    matches on the same normalized keys the blacklist uses (casing, punctuation,
    and a trailing legal suffix all ignored).
    """
    keys = discover.company_keys(name)
    if not keys:
        return None
    for row in session.exec(select(SourceSlug)).all():
        if _slug_company_key(row.slug) in keys:
            return row
    return None


def _blacklist_guard(session: Session, name: str) -> None:
    keys = discover.company_keys(name)
    if not keys:
        return
    blacklisted = {
        n for n in session.exec(select(BlacklistedCompany.normalized_name)).all()
    }
    if keys & blacklisted:
        raise WatchedCompanyBlacklisted(
            f"{name} is on your company blacklist — its jobs would be dropped at "
            "ingest. Remove it from the blacklist first."
        )


def _persist_boards(
    session: Session, boards: list[discover.Board], label: str
) -> list[SourceSlug]:
    now = datetime.now(timezone.utc)
    rows = [
        SourceSlug(
            source=b.source,
            slug=b.slug,
            last_job_count=b.job_count,
            added_by_user=True,
            label=label,
            updated_at=now,
        )
        for b in boards
    ]
    session.add_all(rows)
    session.commit()
    for row in rows:
        session.refresh(row)
    return rows


def _found_message(label: str, rows: list[SourceSlug]) -> str:
    where = ", ".join(
        f"{r.source}"
        + (f" ({r.last_job_count} open)" if r.last_job_count is not None else "")
        for r in rows
    )
    return f"Added {label} — now searching {where}."


def _already_result(typed: str, row: SourceSlug) -> AddWatchedResult:
    """The "we already search this one" notice. Names the board it matched, so a
    wrong match (slugs are all we have to go on) is visible rather than silent."""
    return AddWatchedResult(
        "already_searched",
        f"{typed} is already in your search list ({row.source} / {row.slug}) — "
        "not added again.",
        [row],
    )


def add_watched_company(session: Session, query: str) -> AddWatchedResult:
    """Add one company to the searched list from a name or a pasted board URL.

    A company already being searched is reported back as ``already_searched``
    without a second row being written — including when it got into the list via
    the seed or feed discovery, which is the common case for well-known
    employers. Raises a ``WatchedCompanyError`` subclass when the company can't
    be resolved to a live board.
    """
    text = (query or "").strip()
    if not text:
        raise WatchedCompanyError("Enter a company name or job-board URL.")

    board = discover.parse_board_url(text)
    if board is None and _looks_like_url(text):
        supported = ", ".join(sorted(set(discover.SUPPORTED_LABELS.values())))
        raise WatchedCompanyUnknownUrl(
            f"That doesn't look like a job board I can read. Supported: {supported}."
        )

    if board is not None:
        label = board.slug.split("|")[0]
        _blacklist_guard(session, label)
        # Exact board first so the notice names the row they actually pasted;
        # then the company check, which also catches the same employer already
        # being watched on a different ATS.
        existing = find_watched_board(
            session, board.source, board.slug
        ) or find_watched_by_name(session, label)
        if existing is not None:
            return _already_result(company_display_name(existing), existing)
        exists, count, reason = discover.verify_board(board)
        if not exists:
            label_source = discover.SUPPORTED_LABELS.get(board.source, board.source)
            raise WatchedCompanyUnreachable(f"That {label_source} board {reason}.")
        rows = _persist_boards(session, [discover.Board(board.source, board.slug, count)], label)
        return AddWatchedResult("added", _found_message(label, rows), rows)

    _blacklist_guard(session, text)
    existing = find_watched_by_name(session, text)
    if existing is not None:
        return _already_result(text, existing)

    boards = discover.probe_company(text)
    if not boards:
        supported = ", ".join(sorted(set(discover.SUPPORTED_LABELS.values())))
        raise WatchedCompanyNotFound(
            # No quotes around the name: the UI unwraps this from a JSON error
            # envelope, and escaped quotes survive that trip as visible slashes.
            f"Couldn't find a job board for {text}. Paste the URL of their "
            f"careers page instead ({supported})."
        )
    rows = _persist_boards(session, boards, text)
    return AddWatchedResult("added", _found_message(text, rows), rows)


def remove_watched_company(session: Session, slug_id: int) -> bool:
    """Drop a hand-added board from the searched list. Returns False for an
    unknown id or a row the user didn't add (seed/discovery rows are managed by
    ``refresh-slugs``, not here)."""
    row = session.get(SourceSlug, slug_id)
    if row is None or not row.added_by_user:
        return False
    session.delete(row)
    session.commit()
    return True


def _looks_like_url(text: str) -> bool:
    """Whether to treat the input as a URL the user got wrong, rather than as a
    company name. Deliberately loose: any scheme, or a dotted host-ish token."""
    lowered = text.lower()
    return lowered.startswith(("http://", "https://")) or bool(
        re.match(r"^[\w.-]+\.[a-z]{2,}(/|$)", lowered)
    )
