"""Company-blacklist endpoints: list, add, and remove companies the user never
wants surfaced. The blacklist is applied at ingest time (see
``ingest.ingest_one``); editing it here only affects future ingests, never rows
already persisted.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlmodel import Session

from job_applier import services
from job_applier.api.schemas import BlacklistAddIn, BlacklistedCompanyOut
from job_applier.models.db import BlacklistedCompany, get_session

router = APIRouter(tags=["blacklist"])


def _blacklist_out(c: BlacklistedCompany) -> BlacklistedCompanyOut:
    return BlacklistedCompanyOut(
        id=c.id,
        name=c.name,
        normalized_name=c.normalized_name,
        reason=c.reason,
        created_at=c.created_at,
    )


@router.get("/api/blacklist", response_model=list[BlacklistedCompanyOut])
def list_blacklist(session: Session = Depends(get_session)):
    return [_blacklist_out(c) for c in services.list_blacklisted_companies(session)]


@router.post("/api/blacklist", response_model=BlacklistedCompanyOut)
def add_blacklist(body: BlacklistAddIn, session: Session = Depends(get_session)):
    """Add a company to the ingest blacklist.

    Idempotent on the normalized name: re-adding a company already present (under
    any spelling variant) returns the existing entry rather than erroring.
    """
    try:
        row = services.add_blacklisted_company(session, body.name, body.reason)
    except services.BlacklistNameTooShort as exc:
        raise HTTPException(422, str(exc)) from exc
    return _blacklist_out(row)


@router.delete("/api/blacklist/{blacklist_id}", status_code=204)
def remove_blacklist(blacklist_id: int, session: Session = Depends(get_session)):
    if not services.remove_blacklisted_company(session, blacklist_id):
        raise HTTPException(404, "blacklist entry not found")
    return Response(status_code=204)
