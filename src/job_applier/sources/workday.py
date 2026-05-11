"""Workday Job Board source.

Workday tenants expose a public CXS (Candidate Experience) API at:

    POST https://{tenant}.{region}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs
    GET  https://{tenant}.{region}.myworkdayjobs.com/wday/cxs/{tenant}/{site}{externalPath}

The list call returns only ``title``, ``locationsText``, ``postedOn``, and
``externalPath`` — descriptions require a second per-posting call. Many
tenants have thousands of postings, so we narrow up-front via ``searchText``
queries and pre-filter titles before fetching detail.

Slugs are stored in ``SourceSlug.slug`` as ``{tenant}|{region}|{site}`` —
e.g. ``salesforce|wd12|External_Career_Site``. This packs the three pieces
needed to construct the URL into the existing schema without a migration.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

from job_applier.sources.base import RawJob

log = logging.getLogger(__name__)

# Search terms used to narrow each tenant. The union catches engineering roles
# without forcing us to download every job. We dedupe by externalPath.
SEARCH_TERMS = ["software engineer", "typescript", "javascript", "node"]

# Per-(tenant, search-term) cap. Workday allows ~20 per page; this is the max
# postings we'll page through for a single search.
MAX_PER_SEARCH = 100
PAGE_SIZE = 20

# Senior + engineering title gate, applied before the detail fetch. Cheap regex
# match — anything that gets through still goes through the full filter pipeline.
TITLE_GATE = re.compile(
    r"\b(senior|sr\.?|staff|principal|lead|architect|distinguished|head\s+of)\b.*?"
    r"\b(engineer|developer|architect|sde|swe)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class WorkdayBoard:
    tenant: str
    region: str  # e.g. wd1, wd5, wd12
    site: str   # e.g. External_Career_Site

    @property
    def host(self) -> str:
        return f"{self.tenant}.{self.region}.myworkdayjobs.com"

    @property
    def jobs_url(self) -> str:
        return f"https://{self.host}/wday/cxs/{self.tenant}/{self.site}/jobs"

    def detail_url(self, external_path: str) -> str:
        return f"https://{self.host}/wday/cxs/{self.tenant}/{self.site}{external_path}"

    def public_url(self, external_path: str) -> str:
        return f"https://{self.host}/{self.site}{external_path}"


def parse_slug(slug: str) -> WorkdayBoard | None:
    parts = slug.split("|")
    if len(parts) != 3 or not all(parts):
        return None
    tenant, region, site = (p.strip() for p in parts)
    return WorkdayBoard(tenant=tenant, region=region, site=site)


class WorkdaySource:
    name = "workday"

    def __init__(self, slugs: list[str]) -> None:
        self.boards = [b for s in slugs if (b := parse_slug(s)) is not None]

    def fetch(self) -> Iterable[RawJob]:
        with httpx.Client(
            timeout=30.0,
            follow_redirects=True,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "Mozilla/5.0 (compatible; job-applier/0.1)",
            },
        ) as client:
            for board in self.boards:
                yield from _fetch_board(client, board)


def _fetch_board(client: httpx.Client, board: WorkdayBoard) -> Iterable[RawJob]:
    seen_paths: set[str] = set()
    candidates: list[dict] = []

    for term in SEARCH_TERMS:
        offset = 0
        pulled = 0
        while pulled < MAX_PER_SEARCH:
            try:
                resp = client.post(
                    board.jobs_url,
                    json={
                        "appliedFacets": {},
                        "limit": PAGE_SIZE,
                        "offset": offset,
                        "searchText": term,
                    },
                )
            except httpx.HTTPError as e:
                log.warning("workday[%s] search %r failed: %s", board.tenant, term, e)
                break

            if resp.status_code != 200:
                # 422 means this tenant rejects the body shape — skip it entirely.
                log.warning(
                    "workday[%s] search %r returned HTTP %d, skipping tenant",
                    board.tenant,
                    term,
                    resp.status_code,
                )
                return

            try:
                data = resp.json()
            except ValueError:
                break

            postings = data.get("jobPostings") or []
            if not postings:
                break

            for p in postings:
                path = p.get("externalPath")
                if not path or path in seen_paths:
                    continue
                title = p.get("title") or ""
                if not TITLE_GATE.search(title):
                    seen_paths.add(path)  # mark as seen so we don't recheck
                    continue
                seen_paths.add(path)
                candidates.append(p)

            offset += len(postings)
            pulled += len(postings)
            if len(postings) < PAGE_SIZE:
                break

    log.info(
        "workday[%s] %d candidates after title gate (across %d search terms)",
        board.tenant,
        len(candidates),
        len(SEARCH_TERMS),
    )

    for p in candidates:
        raw = _fetch_detail(client, board, p)
        if raw is not None:
            yield raw
        # Be polite — Workday tenants are real customer infra
        time.sleep(0.05)


def _fetch_detail(
    client: httpx.Client, board: WorkdayBoard, posting: dict
) -> RawJob | None:
    external_path = posting["externalPath"]
    try:
        resp = client.get(board.detail_url(external_path))
        resp.raise_for_status()
        data = resp.json()
    except (httpx.HTTPError, ValueError) as e:
        log.warning(
            "workday[%s] detail fetch failed for %s: %s",
            board.tenant,
            external_path,
            e,
        )
        return None

    info = data.get("jobPostingInfo") or {}
    title = (info.get("title") or posting.get("title") or "").strip()
    if not title:
        return None

    description = info.get("jobDescription") or ""
    location = info.get("location") or posting.get("locationsText") or ""
    remote_type = (info.get("remoteType") or "").lower()
    remote = "remote" in remote_type or "remote" in location.lower()

    job_req_id = info.get("jobReqId") or info.get("id") or external_path
    public_url = info.get("externalUrl") or board.public_url(external_path)

    return RawJob(
        source="workday",
        source_id=f"{board.tenant}:{job_req_id}",
        url=public_url,
        title=title,
        company_name=board.tenant.title(),
        description=description,
        location=location or None,
        remote=remote,
        employment_type=info.get("timeType"),
        posted_at=_parse_date(info.get("startDate") or info.get("postedOn")),
        tags=[t for t in [info.get("timeType"), remote_type] if t],
        raw={"list": posting, "detail": info},
    )


_DATE_FORMATS = ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S")


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    # Try ISO first
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        pass
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    # Strings like "Posted Today" / "Posted 5 Days Ago" — give up rather than guess
    return None
