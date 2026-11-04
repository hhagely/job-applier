"""Pinpoint source.

Per-company public endpoint:
    https://{slug}.pinpointhq.com/postings.json

No auth. Returns ``{"data": [...]}`` with every open posting and its full
description, so a board is one request. Employers often front this with their
own careers domain, but the ``pinpointhq.com`` subdomain still answers. A posting
names a city and province but no country and no posted date, so the filter reads
the country from the location text.

A live subdomain doesn't prove a real board: trial accounts exist under many real
company names, either empty or still holding Pinpoint's sample jobs. See
``is_sample_board``.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable

import httpx

from job_applier.sources.base import RawJob, fetch_boards, looks_remote

log = logging.getLogger(__name__)

API = "https://{slug}.pinpointhq.com/postings.json"

# The body arrives split into these, each with an employer-chosen heading.
_SECTIONS = ("key_responsibilities", "skills_knowledge_expertise", "benefits")

# Every trial account starts with the same jobs, whose locations it may have edited
# ("Head of DEI - UK", "Head of DEI - US", plus a Marketing Manager in Paris).
_SAMPLE_TITLE = re.compile(r"^Head of DEI - ", re.IGNORECASE)


def is_sample_board(postings: list) -> bool:
    """Whether this looks like a trial account rather than an employer's board."""
    return any(
        isinstance(p, dict) and _SAMPLE_TITLE.match(p.get("title") or "") for p in postings
    )


class PinpointSource:
    name = "pinpoint"

    def __init__(self, company_slugs: list[str]) -> None:
        self.company_slugs = company_slugs

    def fetch(self) -> Iterable[RawJob]:
        with httpx.Client(timeout=30.0, follow_redirects=True) as client:
            yield from fetch_boards(
                self.company_slugs, lambda slug: _fetch_board(client, slug), source=self.name
            )


def _fetch_board(client: httpx.Client, slug: str) -> Iterable[RawJob]:
    try:
        resp = client.get(API.format(slug=slug))
        resp.raise_for_status()
        payload = resp.json()
    except (httpx.HTTPError, ValueError) as e:
        log.warning("pinpoint[%s] fetch failed: %s", slug, e)
        return

    postings = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(postings, list):
        log.warning("pinpoint[%s] returned an unexpected payload, skipping", slug)
        return

    for item in postings:
        yield from _normalize(slug, item)


def _normalize(company_slug: str, item: dict) -> Iterable[RawJob]:
    title = (item.get("title") or "").strip()
    job_id = item.get("id")
    if not title or not job_id:
        return

    location = _location(item.get("location"))
    workplace_type = (item.get("workplace_type") or "").lower()
    # A stated workplace type wins, as on Lever and Ashby.
    if workplace_type:
        remote = workplace_type == "remote"
    else:
        remote = looks_remote(location)

    department = ((item.get("job") or {}).get("department") or {}).get("name")

    yield RawJob(
        source="pinpoint",
        source_id=f"{company_slug}:{job_id}",
        url=item.get("url") or "",
        title=title,
        company_name=company_slug.replace("-", " ").title(),
        description=_description(item),
        location=location,
        remote=remote,
        employment_type=item.get("employment_type_text") or None,
        tags=[t for t in (department, workplace_type) if t],
        raw=item,
    )


def _location(loc: object) -> str | None:
    """``"Edinburgh, City of Edinburgh"``: the name plus the province when it adds
    anything, since the province is often what identifies a small town."""
    if not isinstance(loc, dict):
        return None
    parts: list[str] = []
    for value in (loc.get("name"), loc.get("province")):
        if value and value not in parts:
            parts.append(value)
    return ", ".join(parts) or None


def _description(item: dict) -> str:
    chunks = [item.get("description") or ""]
    for section in _SECTIONS:
        body = item.get(section)
        if body:
            header = item.get(f"{section}_header")
            chunks.append(f"<h3>{header}</h3>{body}" if header else body)
    return "\n\n".join(c for c in chunks if c)
