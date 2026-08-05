"""Company-whitelist endpoints: list, add, and remove company job boards the
user wants searched by name.

The mirror of the blacklist. An entry is a ``SourceSlug`` row flagged
``added_by_user``, so ingest picks it up like any discovered board; these routes
only cover the hand-added ones. Adding resolves a company name (or a pasted
board URL) to a live board over the network — see ``sources.discover`` — so the
POST is slower than a normal write, on the order of a few seconds.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlmodel import Session

from job_applier import services
from job_applier.api.schemas import (
    WatchedCompanyAddIn,
    WatchedCompanyAddOut,
    WatchedCompanyOut,
)
from job_applier.models.db import SourceSlug, get_session

router = APIRouter(tags=["watchlist"])


def _watched_out(row: SourceSlug) -> WatchedCompanyOut:
    return WatchedCompanyOut(
        id=row.id,
        source=row.source,
        slug=row.slug,
        label=services.company_display_name(row),
        enabled=row.enabled,
        last_job_count=row.last_job_count,
        last_error=row.last_error,
        added_at=row.added_at,
    )


@router.get("/api/watched-companies", response_model=list[WatchedCompanyOut])
def list_watched(session: Session = Depends(get_session)):
    return [_watched_out(r) for r in services.list_watched_companies(session)]


@router.post("/api/watched-companies", response_model=WatchedCompanyAddOut)
def add_watched(body: WatchedCompanyAddIn, session: Session = Depends(get_session)):
    """Add one company to the searched list.

    A company already being searched — whether the user added it or the seed /
    feed discovery did — comes back as ``already_searched`` with no second row
    written, which is a 200 the UI shows as a notice. A company that can't be
    resolved to a live board is a 422 with the reason.
    """
    try:
        result = services.add_watched_company(session, body.query)
    except services.WatchedCompanyError as exc:
        raise HTTPException(422, str(exc)) from exc
    return WatchedCompanyAddOut(
        status=result.status,
        message=result.message,
        companies=[_watched_out(r) for r in result.companies],
    )


@router.delete("/api/watched-companies/{slug_id}", status_code=204)
def remove_watched(slug_id: int, session: Session = Depends(get_session)):
    if not services.remove_watched_company(session, slug_id):
        raise HTTPException(404, "company not found in your added list")
    return Response(status_code=204)
