"""Workable Job Board source.

Public per-company endpoints (no auth):
    POST https://apply.workable.com/api/v3/accounts/{slug}/jobs   (list, with paging cursor)
    GET  https://apply.workable.com/api/v1/accounts/{slug}/jobs/{shortcode}   (detail with description)

The v3 list endpoint returns job summaries (title, location, workplace, remote
flag, department, shortcode) but no description body. The v1 detail endpoint
returns the rich fields. We fetch the list once per slug, then fan out to the
detail endpoint per job — bounded by ``MAX_JOBS_PER_SLUG`` so an unusually big
careers page can't dominate a run.

Job page URL pattern: ``https://apply.workable.com/{slug}/j/{shortcode}/``
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterable
from datetime import datetime
from typing import Optional

import httpx

from job_applier.filters import FilterConfig, title_quick_fail
from job_applier.sources.base import RawJob

log = logging.getLogger(__name__)

LIST_URL = "https://apply.workable.com/api/v3/accounts/{slug}/jobs"
DETAIL_URL = "https://apply.workable.com/api/v1/accounts/{slug}/jobs/{shortcode}"
JOB_PAGE = "https://apply.workable.com/{slug}/j/{shortcode}/"

# Hard ceiling so one outlier board can't blow up an ingest. Real boards run
# 5-50 open roles; anything above 200 is almost always an aggregator or a
# stale board republishing everything.
MAX_JOBS_PER_SLUG = 200
PAGE_LIMIT = 100

# Workable's detail endpoint rate-limits at ~60 req/min per IP. With the
# title-level pre-filter we make ~10x fewer detail calls per slug, so a short
# backoff + single retry is enough. We still abort the rest of the run on a
# second 429 — by then the cooldown is established and pressing harder makes
# it worse.
RATE_LIMIT_BACKOFF_S = 10.0
MAX_RETRIES_ON_429 = 1
# Once we've eaten the backoff and still hit 429, skip remaining slugs.
LIST_429_SHORT_CIRCUITS_RUN = True


class WorkableSource:
    name = "workable"

    def __init__(
        self,
        company_slugs: list[str],
        filter_config: Optional[FilterConfig] = None,
    ) -> None:
        self.company_slugs = company_slugs
        # When supplied, used to skip the per-job detail fetch for titles that
        # already fail the seniority or sales rules — the dominant cost on
        # this source. Optional so tests/standalone use still work.
        self.filter_config = filter_config
        self._rate_limited = False

    def fetch(self) -> Iterable[RawJob]:
        with httpx.Client(timeout=30.0, follow_redirects=True) as client:
            for slug in self.company_slugs:
                if self._rate_limited and LIST_429_SHORT_CIRCUITS_RUN:
                    # Once we've eaten a 429 with backoff and still hit it again,
                    # spending the rest of the run pounding the API just extends
                    # the cooldown. Bail until the next ingest.
                    log.warning(
                        "workable rate-limited, skipping remaining %d slugs",
                        len(self.company_slugs)
                        - self.company_slugs.index(slug),
                    )
                    return
                yield from self._fetch_slug(client, slug)

    def _fetch_slug(self, client: httpx.Client, slug: str) -> Iterable[RawJob]:
        summaries = list(self._list_jobs(client, slug))
        if not summaries:
            return

        for summary in summaries[:MAX_JOBS_PER_SLUG]:
            shortcode = summary.get("shortcode")
            title = summary.get("title") or ""
            if not shortcode:
                continue
            # Title-level pre-filter — skip the detail fetch when the title
            # alone disqualifies the posting. Cuts detail calls by ~10x on a
            # typical board, which is what keeps us under Workable's IP rate
            # limit.
            if title_quick_fail(title, self.filter_config):
                continue
            detail = self._get_with_backoff(
                client, DETAIL_URL.format(slug=slug, shortcode=shortcode)
            )
            if detail is None:
                # 429-after-retry or a hard failure on this posting — skip it
                # but keep going on other slugs unless _rate_limited tripped.
                if self._rate_limited:
                    return
                continue
            normalized = _normalize(slug, detail)
            if normalized is not None:
                yield normalized

    def _list_jobs(self, client: httpx.Client, slug: str) -> Iterable[dict]:
        next_token: str | None = None
        pulled = 0
        while pulled < MAX_JOBS_PER_SLUG:
            body: dict = {"query": ""}
            if next_token:
                body["token"] = next_token
            payload = self._post_with_backoff(
                client, LIST_URL.format(slug=slug), body, label=f"list[{slug}]"
            )
            if payload is None:
                return
            results = payload.get("results") or []
            if not results:
                return
            for item in results:
                yield item
                pulled += 1
                if pulled >= MAX_JOBS_PER_SLUG:
                    return
            next_token = payload.get("nextPage")
            if not next_token:
                return

    def _get_with_backoff(self, client: httpx.Client, url: str) -> dict | None:
        return self._request_with_backoff(client, "GET", url, None, label=url)

    def _post_with_backoff(
        self, client: httpx.Client, url: str, body: dict, *, label: str
    ) -> dict | None:
        return self._request_with_backoff(client, "POST", url, body, label=label)

    def _request_with_backoff(
        self,
        client: httpx.Client,
        method: str,
        url: str,
        body: dict | None,
        *,
        label: str,
    ) -> dict | None:
        """One retry on 429 with a fixed backoff. Sets ``self._rate_limited``
        when the retry also fails so the caller can short-circuit the run."""
        for attempt in range(MAX_RETRIES_ON_429 + 1):
            try:
                if method == "POST":
                    resp = client.post(url, json=body)
                else:
                    resp = client.get(url)
            except httpx.HTTPError as e:
                log.warning("workable[%s] %s failed: %s", label, method, e)
                return None
            if resp.status_code == 429:
                if attempt >= MAX_RETRIES_ON_429:
                    log.warning(
                        "workable[%s] %s rate-limited after %d retries; aborting run",
                        label,
                        method,
                        MAX_RETRIES_ON_429,
                    )
                    self._rate_limited = True
                    return None
                log.info(
                    "workable[%s] %s 429, sleeping %.0fs (attempt %d)",
                    label,
                    method,
                    RATE_LIMIT_BACKOFF_S,
                    attempt + 1,
                )
                time.sleep(RATE_LIMIT_BACKOFF_S)
                continue
            try:
                resp.raise_for_status()
                return resp.json()
            except (httpx.HTTPError, ValueError) as e:
                log.warning("workable[%s] %s failed: %s", label, method, e)
                return None
        return None


def _normalize(company_slug: str, item: dict) -> RawJob | None:
    title = (item.get("title") or "").strip()
    shortcode = item.get("shortcode")
    if not title or not shortcode:
        return None

    location = item.get("location") or {}
    parts = [
        location.get("city"),
        location.get("region"),
        location.get("country"),
    ]
    location_str = ", ".join(p for p in parts if p) or None

    workplace = (item.get("workplace") or "").strip().lower()
    remote = (
        bool(item.get("remote"))
        or workplace == "remote"
        or "remote" in (location_str or "").lower()
    )

    description_parts = [
        item.get("description") or "",
        item.get("requirements") or "",
        item.get("benefits") or "",
    ]
    description = "\n\n".join(p for p in description_parts if p).strip()

    department = item.get("department") or []
    if isinstance(department, str):
        department = [department]
    tags = [t for t in department if isinstance(t, str)]
    if workplace:
        tags.append(workplace)

    return RawJob(
        source="workable",
        source_id=f"{company_slug}:{shortcode}",
        url=JOB_PAGE.format(slug=company_slug, shortcode=shortcode),
        title=title,
        company_name=company_slug,
        description=description,
        location=location_str,
        remote=remote,
        employment_type=(item.get("type") or None),
        posted_at=_parse_date(item.get("published") or item.get("created")),
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
