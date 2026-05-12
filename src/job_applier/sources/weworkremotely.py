"""We Work Remotely source.

WWR exposes per-category RSS feeds — no auth, no rate limiting documented.
We pull the engineering-heavy categories. Title format is conventionally
``Company: Position`` so we split on the first colon to get a usable
``company_name``.

WWR's own apply flow requires a paid seeker subscription, so the WWR posting
URL is useless for the user. However, the RSS ``<description>`` is the full
HTML body of the posting and frequently contains a direct link to the
company's ATS (Greenhouse, Lever, Ashby, etc.) or careers page. We extract
that link and use it as the canonical ``url``; postings that don't yield one
are dropped.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from datetime import datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit
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

HREF_RE = re.compile(r"""href\s*=\s*["']([^"']+)["']""", re.IGNORECASE)

# Hosts that clearly host job applications. If we find one, use it without
# further deliberation.
ATS_HOSTS = (
    "greenhouse.io",
    "lever.co",
    "ashbyhq.com",
    "myworkdayjobs.com",
    "workable.com",
    "smartrecruiters.com",
    "bamboohr.com",
    "jobvite.com",
    "breezy.hr",
    "jobs.personio.com",
    "recruitee.com",
    "rippling.com",
    "teamtailor.com",
    "pinpointhq.com",
    "join.com",
    "applytojob.com",
    "icims.com",
    "myworkdaysite.com",
)

# Hosts to ignore — these are noise (social, tracking, the WWR site itself,
# image CDNs, etc.) that occasionally appear in description bodies.
SKIP_HOST_SUFFIXES = (
    "weworkremotely.com",
    "twitter.com",
    "x.com",
    "linkedin.com",
    "facebook.com",
    "instagram.com",
    "youtube.com",
    "github.com",
    "medium.com",
    "wikipedia.org",
    "google.com",
    "tinyurl.com",
    "bit.ly",
)


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


def _extract_company_url(description: str) -> str | None:
    """Pick the best external apply/careers URL from a WWR description body.

    Preference order:
      1. Known ATS host (Greenhouse, Lever, Ashby, etc.).
      2. Any other external URL whose host is not in ``SKIP_HOST_SUFFIXES``.

    Returns ``None`` if no candidate is found.
    """
    if not description:
        return None

    candidates: list[str] = []
    for raw in HREF_RE.findall(description):
        href = raw.strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        if not href.startswith(("http://", "https://")):
            continue
        host = urlsplit(href).hostname or ""
        host = host.lower()
        if not host:
            continue
        if any(host == s or host.endswith("." + s) for s in SKIP_HOST_SUFFIXES):
            continue
        candidates.append(href)

    for href in candidates:
        host = (urlsplit(href).hostname or "").lower()
        if any(host == s or host.endswith("." + s) for s in ATS_HOSTS):
            return href

    return candidates[0] if candidates else None


def _normalize(item: ET.Element) -> RawJob | None:
    title_full = _text(item, "title")
    wwr_link = _text(item, "link") or _text(item, "guid")
    if not title_full or not wwr_link:
        return None

    description = _text(item, "description")
    company_url = _extract_company_url(description)
    if not company_url:
        # No external link in the body — applying would require a WWR
        # subscription, so drop the posting.
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
    pub_date = _parse_rfc822(_text(item, "pubDate"))

    return RawJob(
        source="weworkremotely",
        source_id=wwr_link,  # WWR link is the stable per-job ID across runs
        url=company_url,  # but the user clicks through to the company's ATS
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
            "wwr_link": wwr_link,
            "company_url": company_url,
        },
    )


def _parse_rfc822(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
