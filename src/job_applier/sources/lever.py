"""Lever postings source.

Per-company public endpoint:
    https://api.lever.co/v0/postings/{slug}?mode=json

No auth. Returns a JSON array of postings.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import datetime, timezone

import httpx

from job_applier.sources.base import RawJob

log = logging.getLogger(__name__)

API = "https://api.lever.co/v0/postings/{slug}"


class LeverSource:
    name = "lever"

    def __init__(self, company_slugs: list[str]) -> None:
        self.company_slugs = company_slugs

    def fetch(self) -> Iterable[RawJob]:
        with httpx.Client(timeout=30.0, follow_redirects=True) as client:
            for slug in self.company_slugs:
                try:
                    resp = client.get(API.format(slug=slug), params={"mode": "json"})
                    resp.raise_for_status()
                    payload = resp.json()
                except (httpx.HTTPError, ValueError) as e:
                    log.warning("lever[%s] fetch failed: %s", slug, e)
                    continue

                if not isinstance(payload, list):
                    log.warning("lever[%s] returned non-list payload, skipping", slug)
                    continue

                for item in payload:
                    yield from _normalize(slug, item)


def _normalize(company_slug: str, item: dict) -> Iterable[RawJob]:
    title = (item.get("text") or "").strip()
    job_id = item.get("id")
    if not title or not job_id:
        return

    categories = item.get("categories") or {}
    location = categories.get("location") or ""
    all_locations = categories.get("allLocations") or []
    workplace_type = (item.get("workplaceType") or "").lower()
    team = categories.get("team") or ""
    commitment = categories.get("commitment") or ""

    remote = workplace_type == "remote" or "remote" in location.lower() or any(
        "remote" in loc.lower() for loc in all_locations
    )

    description_html = item.get("description") or item.get("descriptionBody") or ""
    description_plain = item.get("descriptionPlain") or ""
    additional = item.get("additional") or ""
    lists = item.get("lists") or []
    list_text = "\n".join(
        f"## {lst.get('text', '')}\n{lst.get('content', '')}" for lst in lists
    )

    full_description = "\n\n".join(filter(None, [description_html, additional, list_text]))
    if not full_description.strip():
        full_description = description_plain

    yield RawJob(
        source="lever",
        source_id=f"{company_slug}:{job_id}",
        url=item.get("hostedUrl") or item.get("applyUrl") or "",
        title=title,
        company_name=company_slug.replace("-", " ").title(),
        description=full_description,
        location=location or (all_locations[0] if all_locations else None),
        remote=remote,
        employment_type=commitment or None,
        posted_at=_parse_lever_date(item.get("createdAt")),
        tags=_tags(team, workplace_type),
        raw=item,
    )


def _tags(*chunks: str) -> list[str]:
    return [c.strip() for c in chunks if c and c.strip()]


def _parse_lever_date(value: int | None) -> datetime | None:
    """Lever uses millisecond Unix timestamps."""
    if not value:
        return None
    try:
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc)
    except (ValueError, OSError, TypeError):
        return None
