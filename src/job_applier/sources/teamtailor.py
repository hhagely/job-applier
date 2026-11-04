"""Teamtailor source.

Per-company public feed:
    https://{slug}.teamtailor.com/jobs.rss

No auth (Teamtailor's JSON API needs a per-company key, the feed doesn't). The
feed carries the full description, the employer's name, and each location's
country, so no per-job request is needed. It returns 100 postings at a time and
pages with ``?offset=`` (``?page=`` is ignored). Boards on Teamtailor's North
America cluster live under ``{name}.na.teamtailor.com``; their slug is ``{name}.na``.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import datetime
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree as ET

import httpx

from job_applier.sources.base import RawJob

log = logging.getLogger(__name__)

FEED = "https://{slug}.teamtailor.com/jobs.rss"

PAGE_SIZE = 100
# A safety stop, far above any real board, in case a feed ignores the offset.
MAX_PAGES = 50

_TT = "{https://teamtailor.com/locations}"


class TeamtailorSource:
    name = "teamtailor"

    def __init__(self, company_slugs: list[str]) -> None:
        self.company_slugs = company_slugs

    def fetch(self) -> Iterable[RawJob]:
        with httpx.Client(timeout=30.0, follow_redirects=True) as client:
            for slug in self.company_slugs:
                yield from _fetch_board(client, slug)


def _fetch_board(client: httpx.Client, slug: str) -> Iterable[RawJob]:
    seen: set[str] = set()
    for page in range(MAX_PAGES):
        try:
            resp = client.get(FEED.format(slug=slug), params={"offset": page * PAGE_SIZE})
            resp.raise_for_status()
            channel = ET.fromstring(resp.content).find("channel")
        except (httpx.HTTPError, ET.ParseError) as e:
            # Log it: a silent stop here truncates the board, and "100 of 130
            # jobs" is indistinguishable from "100 jobs".
            log.warning("teamtailor[%s] fetch failed at page %d: %s", slug, page, e)
            return

        if channel is None:
            log.warning("teamtailor[%s] feed has no channel, skipping", slug)
            return

        company = (channel.findtext("title") or "").strip()
        items = channel.findall("item")
        fresh = [i for i in items if (i.findtext("guid") or "") not in seen]
        seen.update(i.findtext("guid") or "" for i in fresh)
        for item in fresh:
            yield from _normalize(slug, company, item)
        # A short page is the last one; no new postings means the offset was ignored.
        if len(items) < PAGE_SIZE or not fresh:
            return


def _normalize(company_slug: str, company: str, item: ET.Element) -> Iterable[RawJob]:
    title = (item.findtext("title") or "").strip()
    guid = (item.findtext("guid") or "").strip()
    if not title or not guid:
        return

    # "fully" is the only remote value; "hybrid" and "none" both need an office.
    remote_status = (item.findtext("remoteStatus") or "").strip().lower()
    locations = [
        {
            "name": (loc.findtext(f"{_TT}name") or "").strip(),
            "city": (loc.findtext(f"{_TT}city") or "").strip(),
            "country": (loc.findtext(f"{_TT}country") or "").strip(),
        }
        for loc in item.iter(f"{_TT}location")
    ]
    department = (item.findtext(f"{_TT}department") or "").strip()

    yield RawJob(
        source="teamtailor",
        source_id=f"{company_slug}:{guid}",
        url=(item.findtext("link") or "").strip(),
        title=title,
        company_name=company or company_slug.split(".")[0].replace("-", " ").title(),
        description=item.findtext("description") or "",
        location=_location(locations),
        remote=remote_status == "fully",
        posted_at=_parse_date(item.findtext("pubDate")),
        tags=[t for t in (department, remote_status) if t],
        # The description is kept in ``description``; the feed has no JSON form,
        # so these are the structured fields ``posting_countries`` reads.
        raw={"guid": guid, "remoteStatus": remote_status, "locations": locations},
    )


def _location(locations: list[dict]) -> str | None:
    """Every location as ``"City, Country"``, falling back to its name."""
    places: list[str] = []
    for loc in locations:
        city = loc["city"] or loc["name"]
        place = ", ".join(p for p in (city, loc["country"]) if p)
        if place and place not in places:
            places.append(place)
    return "; ".join(places) or None


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
