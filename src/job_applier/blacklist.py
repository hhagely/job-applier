"""The company blacklist: employers one profile never wants to see.

Per profile and applied at match time (``matching`` drops a blacklisted
company's postings for that profile only; they stay stored for everyone else).
Entries key on ``normalize_company``, the same normalizer cross-source dedupe
uses, so "Meta", "Meta Inc" and "Meta, Inc." are one entry. The session's
profile scoping picks the profile. The HTTP edge is ``api/blacklist.py``.
"""

from __future__ import annotations

from typing import Optional

from sqlmodel import Session, select

from job_applier.dedupe import normalize_company
from job_applier.models.db import BlacklistedCompany


class BlacklistNameTooShort(ValueError):
    """A company name that normalizes to fewer than 2 alphanumeric chars — too
    thin to match on reliably at match time, so we refuse to store it."""


def list_blacklisted_companies(session: Session) -> list[BlacklistedCompany]:
    """The active profile's blacklisted companies, ordered case-insensitively by
    name (the session's profile scoping picks the profile)."""
    return list(
        session.exec(
            select(BlacklistedCompany).order_by(BlacklistedCompany.normalized_name)
        ).all()
    )


def add_blacklisted_company(
    session: Session, name: str, reason: Optional[str] = None
) -> BlacklistedCompany:
    """Add a company to the active profile's blacklist, which matching applies
    to that profile only. Idempotent on the normalized name.

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
