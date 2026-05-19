"""SmartRecruiters Job Board source.

Public per-company endpoints (no auth):
    GET https://api.smartrecruiters.com/v1/companies/{slug}/postings                  (list)
    GET https://api.smartrecruiters.com/v1/companies/{slug}/postings/{posting_id}     (detail)

The list response carries summaries with location/employment metadata but no
description. ``jobAd.sections`` on the detail response holds the actual JD as
HTML, split into companyDescription / jobDescription / qualifications /
additionalInformation. We concatenate those for the filter to see.

Job page URL: ``postingUrl`` is provided on each detail response.

Slugs are case-sensitive identifiers like ``Visa``, ``AveryDennison``,
``BoschGroup`` — not lowercased.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import datetime

import httpx

from job_applier.sources.base import RawJob

log = logging.getLogger(__name__)

LIST_URL = "https://api.smartrecruiters.com/v1/companies/{slug}/postings"
DETAIL_URL = "https://api.smartrecruiters.com/v1/companies/{slug}/postings/{posting_id}"

# Mirror the Workable adapter — bounds an unusually big careers page.
MAX_JOBS_PER_SLUG = 200
PAGE_LIMIT = 100


class SmartRecruitersSource:
    name = "smartrecruiters"

    def __init__(self, company_slugs: list[str]) -> None:
        self.company_slugs = company_slugs

    def fetch(self) -> Iterable[RawJob]:
        with httpx.Client(timeout=30.0, follow_redirects=True) as client:
            for slug in self.company_slugs:
                yield from self._fetch_slug(client, slug)

    def _fetch_slug(self, client: httpx.Client, slug: str) -> Iterable[RawJob]:
        for summary in self._list_postings(client, slug):
            posting_id = summary.get("id")
            if not posting_id:
                continue
            try:
                resp = client.get(DETAIL_URL.format(slug=slug, posting_id=posting_id))
                resp.raise_for_status()
                detail = resp.json()
            except (httpx.HTTPError, ValueError) as e:
                log.warning(
                    "smartrecruiters[%s/%s] detail fetch failed: %s",
                    slug,
                    posting_id,
                    e,
                )
                continue
            normalized = _normalize(slug, detail)
            if normalized is not None:
                yield normalized

    def _list_postings(self, client: httpx.Client, slug: str) -> Iterable[dict]:
        offset = 0
        pulled = 0
        while pulled < MAX_JOBS_PER_SLUG:
            try:
                resp = client.get(
                    LIST_URL.format(slug=slug),
                    params={"limit": PAGE_LIMIT, "offset": offset},
                )
                resp.raise_for_status()
                payload = resp.json()
            except (httpx.HTTPError, ValueError) as e:
                log.warning("smartrecruiters[%s] list fetch failed: %s", slug, e)
                return
            content = payload.get("content") or []
            if not content:
                return
            for item in content:
                yield item
                pulled += 1
                if pulled >= MAX_JOBS_PER_SLUG:
                    return
            total_found = payload.get("totalFound") or 0
            offset += len(content)
            if offset >= total_found:
                return


def _normalize(company_slug: str, item: dict) -> RawJob | None:
    name = (item.get("name") or "").strip()
    posting_id = item.get("id")
    if not name or not posting_id:
        return None

    location = item.get("location") or {}
    parts = [
        location.get("fullLocation"),
        ", ".join(
            p for p in [location.get("city"), location.get("region"), location.get("country")] if p
        ),
    ]
    # Prefer the pre-formatted fullLocation when present; fall back to the
    # component join otherwise.
    location_str = next((p for p in parts if p), None)

    remote_flag = bool(location.get("remote"))
    hybrid_flag = bool(location.get("hybrid"))
    remote = remote_flag or (
        "remote" in (location_str or "").lower() and not hybrid_flag
    )

    job_ad = item.get("jobAd") or {}
    sections = job_ad.get("sections") or {}
    description_parts: list[str] = []
    for section_key in (
        "jobDescription",
        "qualifications",
        "additionalInformation",
        "companyDescription",
    ):
        section = sections.get(section_key) or {}
        if isinstance(section, dict):
            text = section.get("text") or ""
            if text:
                description_parts.append(text)
    description = "\n\n".join(description_parts).strip()

    company = item.get("company") or {}
    company_name = company.get("name") or company_slug

    tags: list[str] = []
    industry = item.get("industry") or {}
    if industry.get("label"):
        tags.append(industry["label"])
    department = item.get("department") or {}
    if department.get("label"):
        tags.append(department["label"])
    function = item.get("function") or {}
    if function.get("label"):
        tags.append(function["label"])
    if location.get("remote"):
        tags.append("remote")
    if location.get("hybrid"):
        tags.append("hybrid")

    return RawJob(
        source="smartrecruiters",
        source_id=f"{company_slug}:{posting_id}",
        url=item.get("postingUrl") or item.get("applyUrl") or "",
        title=name,
        company_name=company_name,
        description=description,
        location=location_str,
        remote=remote,
        employment_type=((item.get("typeOfEmployment") or {}).get("label")),
        posted_at=_parse_date(item.get("releasedDate")),
        tags=tags,
        raw=item,
    )


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
