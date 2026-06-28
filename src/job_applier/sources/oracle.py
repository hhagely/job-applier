"""Oracle Recruiting Cloud (ORC) source.

Oracle's own careers site and most "Powered by Oracle Recruiting Cloud"
employers run on Oracle Fusion HCM. The public, candidate-facing job search
is driven by an unauthenticated Candidate Experience (CE) REST API -- the
same call the browser makes when you load a company's careers page. (This is
distinct from the authenticated ``recruitingJobRequisitions`` HCM API, which
needs OAuth and is useless for crawling.)

List call (titles, locations, posting dates, requisition ids)::

    GET https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions
        ?onlyData=true
        &expand=requisitionList.secondaryLocations
        &finder=findReqs;siteNumber={CX_n},limit=N,offset=N,sortBy=POSTING_DATES_DESC[,keyword=...]

The list payload nests the postings under ``items[0].requisitionList`` and a
``TotalJobsCount`` alongside them. Descriptions are NOT in the list response;
each posting needs a second detail call::

    GET https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails
        ?expand=all&onlyData=true&finder=ById;Id="{id}",siteNumber={CX_n}

Like the Workday adapter, we narrow up-front with keyword searches and a title
gate so we don't download every requisition on large tenants (Oracle itself
has thousands).

Slugs are stored in ``SourceSlug.slug`` as ``{host}|{siteNumber}|{siteName}``
with an optional fourth ``|{company}`` field -- e.g.
``careers.oracle.com|CX_45001|jobsearch|Oracle``. ``host`` and ``siteNumber``
drive the API; ``siteName`` builds the public job URL
(``/en/sites/{siteName}/job/{id}``); ``company`` is the display name (derived
from the host when omitted, which is only reliable for vanity hosts like
``careers.oracle.com``).
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser

import httpx

from job_applier.sources.base import RawJob

log = logging.getLogger(__name__)

# Keyword searches used to narrow each site. The union catches engineering
# roles without forcing a download of every requisition. Deduped by req id.
SEARCH_TERMS = ["software engineer", "typescript", "javascript", "node"]

# Per-(site, keyword) cap and page size. Oracle CE allows large limits, but we
# keep pages modest and bounded so a single keyword can't pull a whole tenant.
MAX_PER_SEARCH = 100
PAGE_SIZE = 25

# Senior + engineering title gate, applied before the detail fetch. Mirrors the
# Workday adapter's gate -- cheap pre-filter; anything that passes still goes
# through the full filter pipeline downstream.
TITLE_GATE = re.compile(
    r"\b(senior|sr\.?|staff|principal|lead|architect|distinguished|head\s+of)\b.*?"
    r"\b(engineer|developer|architect|sde|swe)\b",
    re.IGNORECASE,
)

# Subdomain labels that carry no company signal -- skipped when deriving a
# display name from the host.
_NOISE_LABELS = {"careers", "career", "jobs", "job", "www", "eeho", "fa"}


class _Stripper(HTMLParser):
    """Minimal HTML-to-text. Oracle returns descriptions as HTML strings."""

    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self._parts.append(data)

    def text(self) -> str:
        return "".join(self._parts)


def _html_to_text(html: str) -> str:
    if not html:
        return ""
    p = _Stripper()
    try:
        p.feed(html)
    except Exception:  # pragma: no cover - defensive; malformed markup
        return html
    return re.sub(r"\n{3,}", "\n\n", p.text()).strip()


def _derive_company(host: str) -> str:
    """Best-effort display name from a host. Reliable only for vanity hosts."""
    labels = [label for label in host.split(".") if label]
    # Drop the TLD and any leading noise subdomains, then titlecase the most
    # significant remaining label (e.g. careers.oracle.com -> "Oracle").
    meaningful = [
        label
        for label in labels[:-1]  # drop TLD
        if label not in _NOISE_LABELS
    ]
    if not meaningful:
        return host
    return meaningful[0].replace("-", " ").title()


@dataclass(frozen=True)
class OracleSite:
    host: str          # e.g. careers.oracle.com
    site_number: str   # e.g. CX_45001
    site_name: str     # e.g. jobsearch (used only for the public URL)
    company: str       # display name

    @property
    def list_url(self) -> str:
        return (
            f"https://{self.host}/hcmRestApi/resources/latest/"
            "recruitingCEJobRequisitions"
        )

    @property
    def detail_url(self) -> str:
        return (
            f"https://{self.host}/hcmRestApi/resources/latest/"
            "recruitingCEJobRequisitionDetails"
        )

    def list_finder(self, *, keyword: str, limit: int, offset: int) -> str:
        # The CE finder is a semicolon/comma-delimited string, not query params.
        parts = [
            f"siteNumber={self.site_number}",
            f"limit={limit}",
            f"offset={offset}",
            "sortBy=POSTING_DATES_DESC",
        ]
        if keyword:
            parts.append(f"keyword={keyword}")
        return "findReqs;" + ",".join(parts)

    def detail_finder(self, req_id: str) -> str:
        # Id must be wrapped in literal double quotes inside the finder string.
        return f'ById;Id="{req_id}",siteNumber={self.site_number}'

    def public_url(self, req_id: str) -> str:
        return f"https://{self.host}/en/sites/{self.site_name}/job/{req_id}"


def parse_slug(slug: str) -> OracleSite | None:
    """Parse ``{host}|{siteNumber}|{siteName}[|{company}]``.

    The first three fields are required; ``company`` is optional and derived
    from the host when omitted.
    """
    parts = [p.strip() for p in slug.split("|")]
    if len(parts) < 3 or not all(parts[:3]):
        return None
    host, site_number, site_name = parts[0], parts[1], parts[2]
    company = parts[3] if len(parts) >= 4 and parts[3] else _derive_company(host)
    return OracleSite(
        host=host, site_number=site_number, site_name=site_name, company=company
    )


class OracleSource:
    name = "oracle"

    def __init__(self, slugs: list[str]) -> None:
        self.sites = [s for raw in slugs if (s := parse_slug(raw)) is not None]

    def fetch(self) -> Iterable[RawJob]:
        with httpx.Client(
            timeout=30.0,
            follow_redirects=True,
            headers={
                "Accept": "application/json",
                # Oracle CE often sits behind Akamai, which 403s non-browser
                # agents. Present as a browser.
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0 Safari/537.36"
                ),
            },
        ) as client:
            for site in self.sites:
                yield from _fetch_site(client, site)


def _fetch_site(client: httpx.Client, site: OracleSite) -> Iterable[RawJob]:
    seen_ids: set[str] = set()
    candidates: list[dict] = []

    for term in SEARCH_TERMS:
        offset = 0
        pulled = 0
        while pulled < MAX_PER_SEARCH:
            try:
                resp = client.get(
                    site.list_url,
                    params={
                        "onlyData": "true",
                        "expand": "requisitionList.secondaryLocations",
                        "finder": site.list_finder(
                            keyword=term, limit=PAGE_SIZE, offset=offset
                        ),
                    },
                )
            except httpx.HTTPError as e:
                log.warning(
                    "oracle[%s] search %r failed: %s", site.host, term, e
                )
                break

            if resp.status_code != 200:
                log.warning(
                    "oracle[%s] search %r returned HTTP %d, skipping site",
                    site.host,
                    term,
                    resp.status_code,
                )
                return

            try:
                data = resp.json()
            except ValueError:
                break

            postings, total = _parse_list(data)
            if not postings:
                break

            for p in postings:
                req_id = str(p.get("Id") or "").strip()
                if not req_id or req_id in seen_ids:
                    continue
                seen_ids.add(req_id)
                title = p.get("Title") or ""
                if not TITLE_GATE.search(title):
                    continue
                candidates.append(p)

            offset += len(postings)
            pulled += len(postings)
            if len(postings) < PAGE_SIZE or (total is not None and offset >= total):
                break

    log.info(
        "oracle[%s] %d candidates after title gate (across %d search terms)",
        site.host,
        len(candidates),
        len(SEARCH_TERMS),
    )

    for p in candidates:
        raw = _fetch_detail(client, site, p)
        if raw is not None:
            yield raw
        # Be polite -- these are real customer Fusion instances.
        time.sleep(0.05)


def _parse_list(data: dict) -> tuple[list[dict], int | None]:
    """Pull the requisition list and total count out of a CE list response.

    The payload shape is ``{"items": [{"TotalJobsCount": N,
    "requisitionList": [...]}]}``. We tolerate the list also appearing at the
    top level in case a tenant flattens it.
    """
    items = data.get("items")
    if isinstance(items, list) and items:
        first = items[0] or {}
        postings = first.get("requisitionList") or []
        total = first.get("TotalJobsCount")
        return (postings if isinstance(postings, list) else []), total
    top = data.get("requisitionList")
    if isinstance(top, list):
        return top, data.get("TotalJobsCount")
    return [], None


def _fetch_detail(
    client: httpx.Client, site: OracleSite, posting: dict
) -> RawJob | None:
    req_id = str(posting.get("Id") or "").strip()
    if not req_id:
        return None
    try:
        resp = client.get(
            site.detail_url,
            params={
                "expand": "all",
                "onlyData": "true",
                "finder": site.detail_finder(req_id),
            },
        )
        resp.raise_for_status()
        data = resp.json()
    except (httpx.HTTPError, ValueError) as e:
        log.warning("oracle[%s] detail fetch failed for %s: %s", site.host, req_id, e)
        return None

    detail = _first_item(data) or {}
    return _normalize(site, posting, detail)


def _first_item(data: dict) -> dict | None:
    items = data.get("items")
    if isinstance(items, list) and items:
        return items[0] or {}
    # Some tenants return the detail object at the top level.
    if data.get("Id"):
        return data
    return None


def _normalize(site: OracleSite, posting: dict, detail: dict) -> RawJob | None:
    req_id = str(detail.get("Id") or posting.get("Id") or "").strip()
    title = (detail.get("Title") or posting.get("Title") or "").strip()
    if not req_id or not title:
        return None

    description = _html_to_text(
        "\n\n".join(
            part
            for part in (
                detail.get("ExternalDescriptionStr"),
                detail.get("ExternalResponsibilitiesStr"),
                detail.get("ExternalQualificationsStr"),
            )
            if part
        )
    )

    location = (
        detail.get("PrimaryLocation")
        or posting.get("PrimaryLocation")
        or _secondary_locations(posting)
        or ""
    )

    workplace = (
        str(detail.get("WorkplaceTypeCode") or detail.get("WorkplaceType") or "")
    ).upper()
    remote = "REMOTE" in workplace or "remote" in location.lower()

    return RawJob(
        source="oracle",
        source_id=f"{site.host}:{req_id}",
        url=site.public_url(req_id),
        title=title,
        company_name=site.company,
        description=description,
        location=location or None,
        remote=remote,
        employment_type=detail.get("WorkerType") or detail.get("JobType"),
        posted_at=_parse_date(detail.get("PostedDate") or posting.get("PostedDate")),
        tags=[
            t
            for t in (detail.get("JobFamily"), detail.get("Category"), workplace or None)
            if t
        ],
        raw={"list": posting, "detail": detail},
    )


def _secondary_locations(posting: dict) -> str:
    locs = posting.get("secondaryLocations")
    if not isinstance(locs, list):
        return ""
    names = [str(loc.get("Name")) for loc in locs if isinstance(loc, dict) and loc.get("Name")]
    return ", ".join(names)


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None
