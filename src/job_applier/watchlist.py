"""The company whitelist: boards the user added by hand to the searched list.

The mirror image of the blacklist: employers the user specifically wants
searched, added one at a time when feed discovery hasn't picked them up. There's
no separate table: an added company is a ``SourceSlug`` row flagged
``added_by_user``, so ingest picks it up with no special-casing and the
company-coverage count includes it. Unlike the blacklist it's shared by every
profile, since the scrape is. The HTTP edge is ``api/watchlist.py``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal, Optional

from sqlmodel import Session, select

from job_applier.models.db import BlacklistedCompany, SourceSlug
from job_applier.sources import discover


class WatchedCompanyError(ValueError):
    """Base for add-a-company failures the UI should show as a message."""


class WatchedCompanyBlacklisted(WatchedCompanyError):
    """The company is on the active profile's blacklist. Only that profile's
    list is checked: watching it would still feed other profiles, but the
    person adding it would never see its postings, so the add is refused."""


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
            f"{name} is on your company blacklist, so its jobs would never reach "
            "your queue. Remove it from the blacklist first."
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
