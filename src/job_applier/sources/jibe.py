"""Jibe Job Board source.

Jibe (jibeapply.com) is the careers-page front-end iCIMS ships for some of
its enterprise tenants. The list endpoint returns the full JD inline, so we
only ever make one HTTP call per page — no detail fan-out, unlike Workable
or SmartRecruiters.

Public per-tenant endpoint (no auth):
    GET https://{tenant}.jibeapply.com/api/jobs?page={n}

Response shape (the bits we care about):
    {
      "jobs": [{"data": {slug, title, description, qualifications,
                          responsibilities, location_name, country_code,
                          employment_type, posted_date, categories, ...}}],
      "totalCount": <int>,
    }

10 jobs per page, fixed. We page until ``jobs`` comes back empty rather
than computing pages up front — same shape works whether totalCount is
present or not.

Job page URL: ``https://{tenant}.jibeapply.com/jobs/{slug}?lang=en-us``

Tenant slugs are the jibeapply subdomain (e.g. ``githubinc``). Jibe has
no central directory and isn't covered by SimplifyJobs, so the seed list
stays manually curated — refresh-slugs neither discovers nor re-verifies
Jibe tenants for now.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import datetime

import httpx

from job_applier.sources.base import RawJob

log = logging.getLogger(__name__)

LIST_URL = "https://{tenant}.jibeapply.com/api/jobs"
JOB_PAGE = "https://{tenant}.jibeapply.com/jobs/{slug}?lang=en-us"

# Hard ceiling so an outlier tenant can't blow up an ingest. GitHub sits at
# ~100; even the largest Jibe customers we've seen are well under 1000.
MAX_JOBS_PER_TENANT = 1000
MAX_PAGES = 100


class JibeSource:
    name = "jibe"

    def __init__(self, tenant_slugs: list[str]) -> None:
        self.tenant_slugs = tenant_slugs

    def fetch(self) -> Iterable[RawJob]:
        with httpx.Client(timeout=30.0, follow_redirects=True) as client:
            for tenant in self.tenant_slugs:
                yield from self._fetch_tenant(client, tenant)

    def _fetch_tenant(self, client: httpx.Client, tenant: str) -> Iterable[RawJob]:
        pulled = 0
        for page in range(1, MAX_PAGES + 1):
            try:
                resp = client.get(LIST_URL.format(tenant=tenant), params={"page": page})
                resp.raise_for_status()
                payload = resp.json()
            except (httpx.HTTPError, ValueError) as e:
                log.warning("jibe[%s] page %d fetch failed: %s", tenant, page, e)
                return
            jobs = payload.get("jobs") or []
            if not jobs:
                return
            for entry in jobs:
                data = entry.get("data") or {}
                normalized = _normalize(tenant, data)
                if normalized is not None:
                    yield normalized
                pulled += 1
                if pulled >= MAX_JOBS_PER_TENANT:
                    return


def _normalize(tenant: str, item: dict) -> RawJob | None:
    title = (item.get("title") or "").strip()
    slug = item.get("slug") or item.get("req_id")
    if not title or not slug:
        return None

    # ``location_name`` carries the remote/onsite marker as text ("US Remote",
    # "Japan Remote", "San Francisco, CA"), while ``full_location`` strips it.
    # The ``location_type`` field is "ANY" on every row we've inspected, so it
    # carries no signal — don't rely on it.
    location = (
        item.get("location_name")
        or item.get("full_location")
        or item.get("short_location")
    )
    remote = "remote" in (location or "").lower()

    description_parts = [
        item.get("description") or "",
        item.get("responsibilities") or "",
        item.get("qualifications") or "",
    ]
    description = "\n\n".join(p for p in description_parts if p).strip()

    tags: list[str] = []
    for cat in item.get("categories") or []:
        if isinstance(cat, dict) and cat.get("name"):
            tags.append(cat["name"])
    # Jibe scatters classification across tags2..tags7; tags3 has been the
    # most reliable "department" slot in the GitHub data, but we surface
    # whatever's there since adapters elsewhere do the same.
    for key in ("tags3", "tags4", "tags5"):
        vals = item.get(key) or []
        if isinstance(vals, list):
            tags.extend(v for v in vals if isinstance(v, str))
    if remote:
        tags.append("remote")

    company_name = item.get("hiring_organization") or tenant

    return RawJob(
        source="jibe",
        source_id=f"{tenant}:{slug}",
        url=JOB_PAGE.format(tenant=tenant, slug=slug),
        title=title,
        company_name=company_name,
        description=description,
        location=location,
        remote=remote,
        employment_type=(item.get("employment_type") or None),
        posted_at=_parse_date(item.get("posted_date") or item.get("create_date")),
        tags=tags,
        raw=item,
    )


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        # Jibe emits "+0000" without a colon; fromisoformat handles that on
        # 3.11+. Strip a trailing "Z" if it ever appears.
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
