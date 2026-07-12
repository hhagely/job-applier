"""Ashby Job Board source.

Public per-company endpoint:
    https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true

No auth. Returns JSON with a ``jobs`` array. Slugs are case-sensitive — Ashby
boards live under e.g. ``Notion``, ``Linear``, ``Vercel`` (not lowercased).
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

import httpx

from job_applier.sources.base import RawJob, looks_remote, parse_iso_date

log = logging.getLogger(__name__)

API = "https://api.ashbyhq.com/posting-api/job-board/{slug}"


class AshbySource:
    name = "ashby"

    def __init__(self, company_slugs: list[str]) -> None:
        self.company_slugs = company_slugs

    def fetch(self) -> Iterable[RawJob]:
        # OpenAI's board is ~10MB / ~45s — give every Ashby board a generous
        # ceiling rather than tuning per-slug.
        with httpx.Client(timeout=120.0, follow_redirects=True) as client:
            for slug in self.company_slugs:
                try:
                    resp = client.get(
                        API.format(slug=slug),
                        params={"includeCompensation": "true"},
                    )
                    resp.raise_for_status()
                    payload = resp.json()
                except (httpx.HTTPError, ValueError) as e:
                    log.warning("ashby[%s] fetch failed: %s", slug, e)
                    continue

                if not isinstance(payload, dict):
                    log.warning("ashby[%s] returned non-object payload, skipping", slug)
                    continue

                for item in payload.get("jobs", []):
                    if not item.get("isListed", True):
                        continue
                    yield from _normalize(slug, item)


def _normalize(company_slug: str, item: dict) -> Iterable[RawJob]:
    title = (item.get("title") or "").strip()
    job_id = item.get("id")
    if not title or not job_id:
        return

    location = (item.get("location") or "").strip()
    secondary = item.get("secondaryLocations") or []
    secondary_text = ", ".join(
        s.get("location") or "" for s in secondary if isinstance(s, dict)
    )
    workplace_type = (item.get("workplaceType") or "").strip()
    is_remote_flag = bool(item.get("isRemote"))
    remote = (
        is_remote_flag
        or workplace_type.lower() == "remote"
        or looks_remote(location, secondary_text)
    )

    description = item.get("descriptionHtml") or item.get("descriptionPlain") or ""
    department = (item.get("department") or "").strip()
    team = (item.get("team") or "").strip()

    extras = "\n\n".join(filter(None, [department, team, secondary_text, workplace_type]))
    full_description = f"{description}\n\n{extras}".strip() if extras else description

    yield RawJob(
        source="ashby",
        source_id=f"{company_slug}:{job_id}",
        url=item.get("jobUrl") or item.get("applyUrl") or "",
        title=title,
        company_name=company_slug,
        description=full_description,
        location=location or (secondary_text or None),
        remote=remote,
        employment_type=item.get("employmentType"),
        posted_at=parse_iso_date(item.get("publishedAt") or item.get("updatedAt")),
        tags=[t for t in [department, team, workplace_type] if t],
        raw=item,
    )
