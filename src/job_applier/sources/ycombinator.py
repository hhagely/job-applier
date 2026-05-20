"""Y Combinator "Work at a Startup" source via HN jobstories.

WaaS itself has no public JSON API, but HN's firebase API exposes a
``jobstories`` feed of currently-running YC job listings:

    https://hacker-news.firebaseio.com/v0/jobstories.json
    https://hacker-news.firebaseio.com/v0/item/{id}.json

The ``url`` field on each item usually points at a YC company job page
(``ycombinator.com/companies/{slug}/jobs/{posting}``) which embeds a
``schema.org/JobPosting`` JSON-LD block — the only reliable structured
source of the title, description, and location. We skip items whose URL
doesn't match that pattern (some link to a company's own careers page,
where JSON-LD presence is variable).

The HN item title carries a stable ``"<Company> (YC W23) Is Hiring <Role>"``
preamble that gives us the company even when JSON-LD's ``hiringOrganization``
is missing or named weirdly.
"""

from __future__ import annotations

import html as html_lib
import json
import logging
import re
from collections.abc import Iterable
from datetime import datetime

import httpx

from job_applier.sources.base import RawJob

log = logging.getLogger(__name__)

JOBSTORIES_URL = "https://hacker-news.firebaseio.com/v0/jobstories.json"
HN_ITEM_URL = "https://hacker-news.firebaseio.com/v0/item/{id}.json"

YC_JOB_URL_RE = re.compile(
    r"^https?://(?:www\.)?ycombinator\.com/companies/([\w.-]+)/jobs/([\w.-]+)"
)
HN_TITLE_RE = re.compile(
    r"^(?P<company>.+?)\s+\(YC\s+[A-Za-z]+\d{2,4}\)\s+(?:Is\s+)?(?:Hiring|Is\s+Looking\s+For)\s+(?:a\s+|an\s+)?(?P<role>.+?)$",
    re.IGNORECASE,
)
JSON_LD_RE = re.compile(
    r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)

# HN exposes ~hundreds of job items at a time. Cap so a slow YC fetch can't
# stall the rest of the ingest run.
MAX_ITEMS = 250
HTTP_TIMEOUT = 20.0


class YCombinatorSource:
    name = "ycombinator"

    def fetch(self) -> Iterable[RawJob]:
        with httpx.Client(
            timeout=HTTP_TIMEOUT,
            follow_redirects=True,
            headers={"User-Agent": "job-applier/0.1"},
        ) as client:
            try:
                resp = client.get(JOBSTORIES_URL)
                resp.raise_for_status()
                ids = resp.json()
            except (httpx.HTTPError, ValueError) as e:
                log.warning("yc jobstories list fetch failed: %s", e)
                return
            if not isinstance(ids, list):
                log.warning("yc jobstories returned non-list payload")
                return

            for item_id in ids[:MAX_ITEMS]:
                item = self._fetch_item(client, item_id)
                if item is None:
                    continue
                normalized = self._normalize(client, item)
                if normalized is not None:
                    yield normalized

    def _fetch_item(self, client: httpx.Client, item_id: int) -> dict | None:
        try:
            resp = client.get(HN_ITEM_URL.format(id=item_id))
            resp.raise_for_status()
            return resp.json()
        except (httpx.HTTPError, ValueError) as e:
            log.warning("yc hn item %s fetch failed: %s", item_id, e)
            return None

    def _normalize(self, client: httpx.Client, item: dict) -> RawJob | None:
        url = item.get("url") or ""
        match = YC_JOB_URL_RE.match(url)
        if match is None:
            return None  # not a YC job page — skip rather than guess

        slug, job_slug = match.group(1), match.group(2)
        hn_title = item.get("title") or ""
        company, role_from_title = _split_hn_title(hn_title)

        try:
            page = client.get(url)
            page.raise_for_status()
        except httpx.HTTPError as e:
            log.warning("yc job page %s fetch failed: %s", url, e)
            return None

        ld = _extract_jobposting_ld(page.text)
        if ld is None:
            return None

        title = (
            (ld.get("title") or "").strip()
            or role_from_title
            or hn_title
        )
        description = (ld.get("description") or "").strip()

        hiring_org = ld.get("hiringOrganization") or {}
        if isinstance(hiring_org, dict):
            company_name = (hiring_org.get("name") or "").strip() or company or slug
        else:
            company_name = company or slug

        location_str, remote = _extract_location(ld)
        # YC explicitly marks remote roles via jobLocationType — most of these
        # have either "TELECOMMUTE" or a structured location. Fall back to a
        # text scan only when both are missing.
        if not remote and (description or "").lower().count("remote") >= 2:
            remote = True

        return RawJob(
            source="ycombinator",
            source_id=f"{slug}:{job_slug}",
            url=url,
            title=title,
            company_name=company_name,
            description=description,
            location=location_str,
            remote=remote,
            employment_type=(ld.get("employmentType") or None) if isinstance(
                ld.get("employmentType"), str
            ) else None,
            posted_at=_parse_date(ld.get("datePosted")),
            tags=["yc", slug],
            raw={"hn": item, "ld": ld},
        )


def _split_hn_title(title: str) -> tuple[str | None, str | None]:
    """Pull a (company, role) tuple out of an HN job-listing title.

    Returns (None, None) when the title doesn't match the YC pattern.
    """
    title = (title or "").strip()
    if not title:
        return None, None
    m = HN_TITLE_RE.match(title)
    if not m:
        return None, None
    return m.group("company").strip(), m.group("role").strip()


def _extract_jobposting_ld(html: str) -> dict | None:
    """Pull the first ``schema.org/JobPosting`` block out of a YC job page.

    YC consistently emits exactly one JobPosting JSON-LD block per posting.
    We tolerate either a single object or an ``@graph`` containing one.
    """
    for raw in JSON_LD_RE.findall(html):
        # JSON-LD can include literal HTML entities in description bodies, but
        # the JSON parser itself doesn't care — we leave entity decoding to the
        # consumer. Strip stray whitespace, and bail on parse errors quietly.
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        for candidate in _iter_ld_candidates(data):
            if _is_jobposting(candidate):
                return candidate
    return None


def _iter_ld_candidates(data) -> Iterable[dict]:
    if isinstance(data, dict):
        yield data
        graph = data.get("@graph")
        if isinstance(graph, list):
            yield from graph
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                yield from _iter_ld_candidates(item)


def _is_jobposting(d: dict) -> bool:
    t = d.get("@type")
    if isinstance(t, str):
        return t.lower() == "jobposting"
    if isinstance(t, list):
        return any(isinstance(x, str) and x.lower() == "jobposting" for x in t)
    return False


def _extract_location(ld: dict) -> tuple[str | None, bool]:
    job_location_type = ld.get("jobLocationType")
    remote_flag = (
        isinstance(job_location_type, str)
        and job_location_type.upper() == "TELECOMMUTE"
    )

    loc = ld.get("jobLocation")
    if loc is None:
        return ("Remote" if remote_flag else None), remote_flag
    if isinstance(loc, list):
        loc = loc[0] if loc else None
    if not isinstance(loc, dict):
        return ("Remote" if remote_flag else None), remote_flag

    address = loc.get("address") or {}
    if isinstance(address, dict):
        parts = [
            address.get("addressLocality"),
            address.get("addressRegion"),
            address.get("addressCountry"),
        ]
        location_str = ", ".join(
            html_lib.unescape(p) for p in parts if isinstance(p, str) and p
        ) or None
    else:
        location_str = None

    return (location_str or ("Remote" if remote_flag else None)), remote_flag


def _parse_date(value) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
