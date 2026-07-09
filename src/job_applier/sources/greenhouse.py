"""Greenhouse Job Boards source.

Per-company public endpoint:
    https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true

No auth required. The `content` field is HTML-entity-encoded — we unescape it
before passing along so downstream consumers see real HTML.
"""

from __future__ import annotations

import html
import logging
from collections.abc import Iterable

import httpx

from job_applier.sources.base import RawJob, parse_iso_date

log = logging.getLogger(__name__)

API = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"


class GreenhouseSource:
    name = "greenhouse"

    def __init__(self, company_slugs: list[str]) -> None:
        self.company_slugs = company_slugs

    def fetch(self) -> Iterable[RawJob]:
        with httpx.Client(timeout=30.0, follow_redirects=True) as client:
            for slug in self.company_slugs:
                try:
                    resp = client.get(API.format(slug=slug), params={"content": "true"})
                    resp.raise_for_status()
                    payload = resp.json()
                except (httpx.HTTPError, ValueError) as e:
                    log.warning("greenhouse[%s] fetch failed: %s", slug, e)
                    continue

                if not isinstance(payload, dict):
                    log.warning("greenhouse[%s] returned non-object payload, skipping", slug)
                    continue

                for item in payload.get("jobs", []):
                    yield from _normalize(slug, item)


def _normalize(company_slug: str, item: dict) -> Iterable[RawJob]:
    title = (item.get("title") or "").strip()
    if not title:
        return

    location_name = (item.get("location") or {}).get("name", "") or ""
    offices = item.get("offices") or []
    office_names = " ".join(o.get("name", "") for o in offices)

    location_blob = f"{location_name} {office_names}".strip()
    remote = _looks_remote(location_blob)

    departments = ", ".join(d.get("name", "") for d in (item.get("departments") or []))
    metadata_kv = " ".join(
        f"{m.get('name', '')}: {m.get('value', '')}" for m in (item.get("metadata") or [])
    )
    description = html.unescape(item.get("content") or "")
    # Append office + dept context so the filter regex can see it.
    extras = "\n\n".join(filter(None, [departments, metadata_kv, office_names]))
    description = f"{description}\n\n{extras}".strip()

    yield RawJob(
        source="greenhouse",
        source_id=f"{company_slug}:{item['id']}",
        url=item.get("absolute_url") or "",
        title=title,
        company_name=item.get("company_name") or company_slug.replace("-", " ").title(),
        description=description,
        location=location_name or office_names or None,
        remote=remote,
        employment_type=None,
        posted_at=parse_iso_date(item.get("updated_at") or item.get("first_published")),
        tags=_tags(departments, metadata_kv),
        raw=item,
    )


def _looks_remote(location_blob: str) -> bool:
    s = location_blob.lower()
    return "remote" in s or "anywhere" in s or "distributed" in s


def _tags(*chunks: str) -> list[str]:
    out: list[str] = []
    for c in chunks:
        for piece in c.replace(":", ",").split(","):
            piece = piece.strip()
            if piece:
                out.append(piece)
    return out
