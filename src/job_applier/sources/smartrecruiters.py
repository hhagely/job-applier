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
from typing import Optional

import httpx

from job_applier.countries import Market
from job_applier.filters import FilterConfig, evaluate_shared, title_quick_fail
from job_applier.models.db import FilterStatus
from job_applier.sources.base import RawJob, fetch_boards, looks_remote, parse_iso_date

log = logging.getLogger(__name__)

LIST_URL = "https://api.smartrecruiters.com/v1/companies/{slug}/postings"
DETAIL_URL = "https://api.smartrecruiters.com/v1/companies/{slug}/postings/{posting_id}"

# Mirror the Workable adapter — bounds an unusually big careers page.
MAX_JOBS_PER_SLUG = 200
PAGE_LIMIT = 100


class SmartRecruitersSource:
    name = "smartrecruiters"

    def __init__(
        self,
        company_slugs: list[str],
        filter_config: Optional[FilterConfig] = None,
        markets: Optional[tuple[Market, ...]] = None,
    ) -> None:
        self.company_slugs = company_slugs
        # Used to skip per-posting detail fetches when the title alone
        # disqualifies. With 173 seeded slugs this is what keeps the run
        # under ~10 minutes instead of nearly an hour.
        self.filter_config = filter_config
        # Every profile's countries unioned, to skip the detail fetch for a posting
        # whose list entry already fails the shared rules. Omitted, nothing is
        # skipped this way, so a caller reporting on drops still sees them all.
        self.markets = markets

    def fetch(self) -> Iterable[RawJob]:
        with httpx.Client(timeout=30.0, follow_redirects=True) as client:
            yield from fetch_boards(
                self.company_slugs,
                lambda slug: self._fetch_slug(client, slug),
                source=self.name,
            )

    def _fetch_slug(self, client: httpx.Client, slug: str) -> Iterable[RawJob]:
        for summary in self._list_postings(client, slug):
            posting_id = summary.get("id")
            title = summary.get("name") or ""
            if not posting_id:
                continue
            # Title-level pre-filter — cheap drop for non-senior or sales
            # postings. The list response carries enough metadata to make
            # this decision without a second HTTP call.
            if title_quick_fail(title, self.filter_config):
                continue
            if self._list_entry_fails(slug, summary):
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

    def _list_entry_fails(self, slug: str, summary: dict) -> bool:
        """Whether a list entry can't pass the shared rules whatever its detail says.

        The list carries the same ``location`` block as the detail: compared on
        3,521 real postings (2026-11-04), location and remote flag never differed,
        and this skipped 96% of the detail fetches with none wrongly dropped. An entry
        with no location block says nothing, so it still gets its detail fetch.
        """
        if not self.markets or not summary.get("location"):
            return False
        listed = _normalize(slug, summary)
        return (
            listed is not None
            and evaluate_shared(listed, self.markets).status is FilterStatus.dropped
        )

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
            if not isinstance(payload, dict):
                log.warning("smartrecruiters[%s] returned non-object payload, skipping", slug)
                return
            content = payload.get("content")
            if not isinstance(content, list):
                # Distinct from the empty-page case below: this abandons the
                # whole board, so it can't be silent.
                log.warning(
                    "smartrecruiters[%s] list has a non-array 'content', skipping", slug
                )
                return
            if not content:
                return
            for item in content:
                pulled += 1
                # Non-dict entries would blow up the caller's ``.get`` and
                # abandon every remaining slug, so drop them here.
                if isinstance(item, dict):
                    yield item
                if pulled >= MAX_JOBS_PER_SLUG:
                    return
            # ``totalFound`` is untrusted scraped JSON, so it's coerced. An
            # unknown total must not collapse to 0 -- ``offset >= 0`` is always
            # true, which would truncate the board to its first page and read
            # exactly like "this company has 100 jobs". Unknown means "keep
            # paging"; the empty-page check above and MAX_JOBS_PER_SLUG bound it.
            raw_total = payload.get("totalFound")
            total_found = _as_int(raw_total)
            if total_found is None and raw_total is not None:
                log.warning(
                    "smartrecruiters[%s] non-numeric totalFound %r, "
                    "paging until the board runs out",
                    slug,
                    raw_total,
                )
            offset += len(content)
            if total_found is not None and offset >= total_found:
                return


def _as_int(value: object) -> int | None:
    """Coerce a scraped count to ``int``; ``None`` when it isn't numeric."""
    try:
        return int(value)  # type: ignore[call-overload]
    except (TypeError, ValueError):
        return None


def _normalize(company_slug: str, item: dict) -> RawJob | None:
    if not isinstance(item, dict):
        return None
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
    remote = remote_flag or (looks_remote(location_str) and not hybrid_flag)

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
        posted_at=parse_iso_date(item.get("releasedDate")),
        tags=tags,
        raw=item,
    )
