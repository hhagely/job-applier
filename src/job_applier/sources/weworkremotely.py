"""We Work Remotely source.

WWR exposes per-category RSS feeds — no auth, no rate limiting documented.
We pull the engineering-heavy categories. Title format is conventionally
``Company: Position`` so we split on the first colon to get a usable
``company_name``.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from datetime import datetime
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree as ET

import httpx

from job_applier.sources.base import RawJob

log = logging.getLogger(__name__)

FEEDS = [
    "https://weworkremotely.com/categories/remote-programming-jobs.rss",
    "https://weworkremotely.com/categories/remote-full-stack-programming-jobs.rss",
    "https://weworkremotely.com/categories/remote-front-end-programming-jobs.rss",
    "https://weworkremotely.com/categories/remote-back-end-programming-jobs.rss",
    "https://weworkremotely.com/categories/remote-devops-sysadmin-jobs.rss",
    "https://weworkremotely.com/categories/remote-management-and-finance-jobs.rss",
]

# WWR titles look like "Company Name: Senior Engineer". Split conservatively —
# only treat the first colon as a separator (positions can contain colons too).
TITLE_SPLIT = re.compile(r"^\s*(?P<company>[^:]+?)\s*:\s*(?P<position>.+)\s*$")


class WeWorkRemotelySource:
    name = "weworkremotely"

    def fetch(self) -> Iterable[RawJob]:
        seen: set[str] = set()  # dedupe within this run — same job appears in multiple feeds
        with httpx.Client(timeout=30.0, follow_redirects=True) as client:
            for url in FEEDS:
                try:
                    resp = client.get(url)
                    resp.raise_for_status()
                except httpx.HTTPError as e:
                    log.warning("weworkremotely[%s] fetch failed: %s", url, e)
                    continue

                try:
                    root = ET.fromstring(resp.content)
                except ET.ParseError as e:
                    log.warning("weworkremotely[%s] XML parse failed: %s", url, e)
                    continue

                for item in root.findall("./channel/item"):
                    raw = _normalize(item)
                    if raw is None or raw.source_id in seen:
                        continue
                    seen.add(raw.source_id)
                    yield raw


def _text(item: ET.Element, tag: str) -> str:
    el = item.find(tag)
    return (el.text or "").strip() if el is not None and el.text else ""


def _normalize(item: ET.Element) -> RawJob | None:
    title_full = _text(item, "title")
    link = _text(item, "link") or _text(item, "guid")
    if not title_full or not link:
        return None

    m = TITLE_SPLIT.match(title_full)
    if m:
        company = m.group("company").strip()
        position = m.group("position").strip()
    else:
        company = "Unknown"
        position = title_full

    region = _text(item, "region")
    category = _text(item, "category")
    description = _text(item, "description")
    pub_date = _parse_rfc822(_text(item, "pubDate"))

    return RawJob(
        source="weworkremotely",
        source_id=link,  # link is the canonical per-job URL
        url=link,
        title=position,
        company_name=company,
        description=description,
        location=region or "Remote",
        remote=True,  # WWR is remote-only
        employment_type=None,
        posted_at=pub_date,
        tags=[t for t in [category] if t],
        raw={
            "title": title_full,
            "region": region,
            "category": category,
            "link": link,
        },
    )


def _parse_rfc822(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
