"""Adzuna jobs source.

Free tier requires (app_id, app_key) at https://developer.adzuna.com.
Set JOB_APPLIER_ADZUNA_APP_ID and JOB_APPLIER_ADZUNA_APP_KEY in the env.
If either is missing, this source skips itself silently — `make ingest`
keeps working without Adzuna configured.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import datetime

import httpx

from job_applier.config import settings
from job_applier.sources.base import RawJob

log = logging.getLogger(__name__)

API = "https://api.adzuna.com/v1/api/jobs/{country}/search/{page}"


class AdzunaSource:
    name = "adzuna"

    def __init__(
        self,
        what: str = "javascript typescript",
        results_per_page: int = 50,
        full_time_only: bool = True,
    ) -> None:
        self.what = what
        self.results_per_page = results_per_page
        self.full_time_only = full_time_only

    def fetch(self) -> Iterable[RawJob]:
        if not settings.adzuna_app_id or not settings.adzuna_app_key:
            log.info("adzuna: app_id/app_key not set, skipping")
            return

        params_base: dict[str, str | int] = {
            "app_id": settings.adzuna_app_id,
            "app_key": settings.adzuna_app_key,
            "what_or": self.what,
            "results_per_page": self.results_per_page,
            "content-type": "application/json",
        }
        if self.full_time_only:
            params_base["full_time"] = 1

        with httpx.Client(timeout=30.0, follow_redirects=True) as client:
            for page in range(1, settings.adzuna_pages + 1):
                url = API.format(country=settings.adzuna_country, page=page)
                try:
                    resp = client.get(url, params=params_base)
                    resp.raise_for_status()
                except httpx.HTTPError as e:
                    log.warning("adzuna page %d failed: %s", page, e)
                    return

                results = resp.json().get("results", [])
                if not results:
                    return
                for item in results:
                    yield _normalize(item)


def _normalize(item: dict) -> RawJob:
    company_name = ((item.get("company") or {}).get("display_name") or "Unknown").strip()
    location_name = (item.get("location") or {}).get("display_name") or ""
    description = item.get("description") or ""
    title = (item.get("title") or "").strip()

    return RawJob(
        source="adzuna",
        source_id=str(item.get("id") or item.get("redirect_url") or ""),
        url=item.get("redirect_url") or "",
        title=title,
        company_name=company_name,
        description=description,
        location=location_name or None,
        remote="remote" in (location_name + " " + title + " " + description).lower(),
        employment_type=item.get("contract_type"),
        posted_at=_parse_date(item.get("created")),
        tags=[item.get("category", {}).get("label")] if item.get("category") else [],
        raw=item,
    )


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
