"""Oracle Recruiting Cloud (ORC) source.

Oracle's own careers site and most "Powered by Oracle Recruiting Cloud"
employers run on Oracle Fusion HCM. The public, candidate-facing job search
is driven by an unauthenticated Candidate Experience (CE) REST API -- the
same call the browser makes when you load a company's careers page. (This is
distinct from the authenticated ``recruitingJobRequisitions`` HCM API, which
needs OAuth and is useless for crawling.)

Crucially, the REST API is served by the underlying Fusion host
(e.g. ``eeho.fa.us2.oraclecloud.com``), NOT by any vanity careers domain.
``careers.oracle.com`` is just a front-end: it serves the candidate UI but
302-redirects ``/hcmRestApi`` calls to the Fusion host's 404 page. So the
slug carries the Fusion API host, and a separate public base URL is used for
human-clickable job links.

List call (titles, locations, posting dates, requisition ids)::

    GET https://{apiHost}/hcmRestApi/resources/latest/recruitingCEJobRequisitions
        ?onlyData=true
        &expand=requisitionList.secondaryLocations
        &finder=findReqs;siteNumber={CX_n},limit=N,offset=N,sortBy=POSTING_DATES_DESC

The list payload nests the postings under ``items[0].requisitionList`` and a
``TotalJobsCount`` alongside them. We page through the whole site (sites are
typically small -- low thousands) and title-gate client-side; descriptions
are NOT in the list response, so each gated posting needs a second detail
call::

    GET https://{apiHost}/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails
        ?expand=all&onlyData=true&finder=ById;Id="{id}",siteNumber={CX_n}

Slugs are stored in ``SourceSlug.slug`` as
``{apiHost}|{siteNumber}|{publicJobBaseUrl}[|{company}]`` -- e.g.
``eeho.fa.us2.oraclecloud.com|CX_45001|https://careers.oracle.com/en/sites/jobsearch|Oracle``.
``apiHost`` and ``siteNumber`` drive the API; ``publicJobBaseUrl`` builds the
click-through link (``{base}/job/{id}``); ``company`` is the display name
(derived from the API host when omitted, which is only a guess).
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Iterable
from dataclasses import dataclass

import httpx

from job_applier.sources.base import TITLE_GATE, RawJob, parse_date_multi

log = logging.getLogger(__name__)

# Page size for the list call and a hard cap on how many postings we'll page
# through per site, so a misbehaving tenant can't spin forever.
PAGE_SIZE = 100
MAX_POSTINGS = 4000

# Explicit "this is a remote role" markers in the title or description. Many
# Oracle tenants (Oracle Health, notably) leave ``WorkplaceType`` blank and
# instead encode the work mode in the title -- "(Remote)", "[Remote]",
# "... - Remote", or a "fully remote" / "work from home" phrase in the body.
# Without this fallback those roles look on-site and get dropped by the hard
# filter. Patterns are kept high-precision: the dash form requires a space
# after the separator so it doesn't fire on "non-remote", and only unambiguous
# body phrases count.
_REMOTE_SIGNAL = re.compile(
    r"\(\s*remote\b"  # "(Remote)" / "(Remote, US)"
    r"|\[\s*remote\b"  # "[Remote]"
    r"|[-–—|:]\s+remote\b"  # " - Remote" / " | Remote" (space skips "non-remote")
    r"|\bremote\s*[-–—|:]"  # "Remote -" / "Remote:"
    r"|\bfully[\s-]+remote\b"
    r"|\b100%\s+remote\b"
    r"|\bremote[\s-]+first\b"
    r"|\bwork(?:s|ing)?\s+remotely\b"
    r"|\bwork\s+from\s+home\b",
    re.IGNORECASE,
)

# A location entry that names only a country, with no city or state component,
# is how Oracle tenants encode a country-wide / remote requisition: a bare
# "United States" instead of (or alongside) "City, ST, United States". On-site
# roles always carry the city-level form, so the comma-free country string is a
# reliable remote signal. Matched against each *individual* location entry, not
# the joined display string, so a remote req that also lists office cities (a
# bare "United States" sitting next to "Austin, TX, United States") is caught.
_COUNTRY_ONLY_LOCATION = re.compile(
    r"^(united states(?: of america)?|usa|u\.s\.a?\.?)$",
    re.IGNORECASE,
)

# Subdomain labels that carry no company signal -- skipped when deriving a
# display name from the host. ``_REGION_LABEL`` matches Fusion datacenter codes
# like ``us2`` / ``em2`` / ``ap1``.
_NOISE_LABELS = {"careers", "career", "jobs", "job", "www", "eeho", "fa", "oraclecloud"}
_REGION_LABEL = re.compile(r"^[a-z]{2}\d+$")


def _combine_description(detail: dict) -> str:
    """Join Oracle's HTML description sections into one renderable blob.

    Oracle returns each section -- description, responsibilities,
    qualifications -- as a standalone HTML string (``<p>``/``<ul>``/``<li>``
    markup, not plain text). We keep the markup rather than stripping it: the
    frontend renders job descriptions with ``{@html}`` and styles the block
    elements, so plain text would collapse into one unformatted wall. Sections
    are separated by a blank line so concatenated ``<p>``/``<ul>`` blocks stay
    distinct, matching how the other adapters (Greenhouse, Workday, Ashby,
    Workable) store HTML descriptions.
    """
    parts = [
        text
        for part in (
            detail.get("ExternalDescriptionStr"),
            detail.get("ExternalResponsibilitiesStr"),
            detail.get("ExternalQualificationsStr"),
        )
        if part and (text := str(part).strip())
    ]
    return "\n\n".join(parts)


def _derive_company(host: str) -> str:
    """Best-effort display name from a host. Only a guess for Fusion hosts."""
    labels = [label for label in host.split(".") if label]
    meaningful = [
        label
        for label in labels[:-1]  # drop TLD
        if label not in _NOISE_LABELS and not _REGION_LABEL.match(label)
    ]
    if not meaningful:
        return host
    return meaningful[0].replace("-", " ").title()


@dataclass(frozen=True)
class OracleSite:
    api_host: str       # Fusion host that serves the REST API, e.g. eeho.fa.us2.oraclecloud.com
    site_number: str    # e.g. CX_45001
    public_base: str    # job-link base, e.g. https://careers.oracle.com/en/sites/jobsearch
    company: str        # display name

    @property
    def list_url(self) -> str:
        return (
            f"https://{self.api_host}/hcmRestApi/resources/latest/"
            "recruitingCEJobRequisitions"
        )

    @property
    def detail_url(self) -> str:
        return (
            f"https://{self.api_host}/hcmRestApi/resources/latest/"
            "recruitingCEJobRequisitionDetails"
        )

    def list_finder(self, *, limit: int, offset: int) -> str:
        # The CE finder is a semicolon/comma-delimited string, not query params.
        return (
            f"findReqs;siteNumber={self.site_number},limit={limit},"
            f"offset={offset},sortBy=POSTING_DATES_DESC"
        )

    def detail_finder(self, req_id: str) -> str:
        # Id must be wrapped in literal double quotes inside the finder string.
        return f'ById;Id="{req_id}",siteNumber={self.site_number}'

    def public_url(self, req_id: str) -> str:
        return f"{self.public_base.rstrip('/')}/job/{req_id}"


def parse_slug(slug: str) -> OracleSite | None:
    """Parse ``{apiHost}|{siteNumber}|{publicJobBaseUrl}[|{company}]``.

    The first three fields are required; ``company`` is optional and derived
    from the API host when omitted.
    """
    parts = [p.strip() for p in slug.split("|")]
    if len(parts) < 3 or not all(parts[:3]):
        return None
    api_host, site_number, public_base = parts[0], parts[1], parts[2]
    company = parts[3] if len(parts) >= 4 and parts[3] else _derive_company(api_host)
    return OracleSite(
        api_host=api_host,
        site_number=site_number,
        public_base=public_base,
        company=company,
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

    offset = 0
    while offset < MAX_POSTINGS:
        try:
            resp = client.get(
                site.list_url,
                params={
                    "onlyData": "true",
                    "expand": "requisitionList.secondaryLocations",
                    "finder": site.list_finder(limit=PAGE_SIZE, offset=offset),
                },
            )
        except httpx.HTTPError as e:
            log.warning("oracle[%s] list fetch failed: %s", site.api_host, e)
            break

        if resp.status_code != 200:
            log.warning(
                "oracle[%s] list returned HTTP %d at offset %d, skipping site",
                site.api_host,
                resp.status_code,
                offset,
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
            if TITLE_GATE.search(title):
                candidates.append(p)

        offset += len(postings)
        if len(postings) < PAGE_SIZE or (total is not None and offset >= total):
            break

    log.info(
        "oracle[%s] %d candidates after title gate (of %d scanned)",
        site.api_host,
        len(candidates),
        len(seen_ids),
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
        log.warning(
            "oracle[%s] detail fetch failed for %s: %s", site.api_host, req_id, e
        )
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

    description = _combine_description(detail)

    location = (
        detail.get("PrimaryLocation")
        or posting.get("PrimaryLocation")
        or _secondary_locations(posting)
        or ""
    )

    workplace = (
        str(detail.get("WorkplaceTypeCode") or detail.get("WorkplaceType") or "")
    ).upper()
    # ``WorkplaceType`` is authoritative when present, but is often blank on
    # Oracle tenants; fall back to the location text, then to a bare-country
    # location entry (see ``_COUNTRY_ONLY_LOCATION``), then to an explicit
    # remote marker in the title/description (see ``_REMOTE_SIGNAL``).
    remote = (
        "REMOTE" in workplace
        or "remote" in location.lower()
        or any(_COUNTRY_ONLY_LOCATION.match(p) for p in _location_entries(posting, detail))
        or bool(_REMOTE_SIGNAL.search(f"{title}\n{description}"))
    )

    return RawJob(
        source="oracle",
        source_id=f"{site.api_host}:{req_id}",
        url=site.public_url(req_id),
        title=title,
        company_name=site.company,
        description=description,
        location=location or None,
        remote=remote,
        employment_type=detail.get("WorkerType") or detail.get("JobType"),
        posted_at=parse_date_multi(detail.get("PostedDate") or posting.get("PostedDate")),
        tags=[
            t
            for t in (detail.get("JobFamily"), detail.get("Category"), workplace or None)
            if t
        ],
        raw={"list": posting, "detail": detail},
    )


def _location_entries(posting: dict, detail: dict) -> list[str]:
    """All individual location strings on a posting, primary plus secondaries.

    Used for the country-only remote check, which has to inspect each entry on
    its own -- a req can list specific offices *and* a bare "United States" --
    rather than the joined display string built in ``_normalize``.
    """
    entries: list[str] = []
    for value in (detail.get("PrimaryLocation"), posting.get("PrimaryLocation")):
        if value:
            entries.append(str(value).strip())
    for source in (detail, posting):
        locs = source.get("secondaryLocations")
        if isinstance(locs, list):
            for loc in locs:
                if isinstance(loc, dict) and loc.get("Name"):
                    entries.append(str(loc["Name"]).strip())
    return entries


def _secondary_locations(posting: dict) -> str:
    locs = posting.get("secondaryLocations")
    if not isinstance(locs, list):
        return ""
    names = [
        str(loc.get("Name"))
        for loc in locs
        if isinstance(loc, dict) and loc.get("Name")
    ]
    return ", ".join(names)
