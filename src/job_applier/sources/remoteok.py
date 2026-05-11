"""RemoteOK source.

Public endpoint:
    https://remoteok.com/api

Returns a JSON array. The first element is metadata (a legal/notice block);
the remainder are postings. No auth required, but the API ToS asks API consumers
to identify themselves via User-Agent and to link back to the source URL — we
already store ``url`` and surface it from the UI.
"""

from __future__ import annotations

import html
import logging
from collections.abc import Iterable
from datetime import datetime, timezone

import httpx

from job_applier.sources.base import RawJob

log = logging.getLogger(__name__)

API = "https://remoteok.com/api"
USER_AGENT = "job-applier (personal job board; +https://github.com/hhagely/job-applier)"


class RemoteOKSource:
    name = "remoteok"

    def fetch(self) -> Iterable[RawJob]:
        try:
            with httpx.Client(
                timeout=30.0,
                follow_redirects=True,
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            ) as client:
                resp = client.get(API)
                resp.raise_for_status()
                payload = resp.json()
        except (httpx.HTTPError, ValueError) as e:
            log.warning("remoteok fetch failed: %s", e)
            return

        if not isinstance(payload, list):
            log.warning("remoteok returned non-list payload, skipping")
            return

        for item in payload:
            if not isinstance(item, dict) or "id" not in item or "position" not in item:
                continue  # metadata header or malformed entry
            yield from _normalize(item)


def _normalize(item: dict) -> Iterable[RawJob]:
    title = html.unescape((item.get("position") or "").strip())
    if not title:
        return

    company = html.unescape((item.get("company") or "").strip()) or "Unknown"
    location = html.unescape((item.get("location") or "").strip()) or "Remote"
    description = item.get("description") or ""
    tags = [t for t in (item.get("tags") or []) if isinstance(t, str)]

    yield RawJob(
        source="remoteok",
        source_id=str(item["id"]),
        url=item.get("url") or item.get("apply_url") or "",
        title=title,
        company_name=company,
        description=description,
        location=location,
        remote=True,  # RemoteOK is remote-only by definition
        employment_type=None,
        posted_at=_parse_date(item.get("date") or item.get("epoch")),
        tags=tags,
        raw=item,
    )


def _parse_date(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (ValueError, OSError):
            return None
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None
