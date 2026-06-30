"""Slug discovery + verification for per-company ingest sources.

The runtime source of truth for which slugs to fetch is the ``SourceSlug``
table. This module fills that table — either from a small built-in seed
(used on fresh ``job-applier init``) or from the SimplifyJobs community
feed (the ``refresh-slugs`` CLI command).

Discovery is Greenhouse + Lever only (SimplifyJobs only carries those slugs).
Re-verification covers all four per-company sources (Greenhouse, Lever,
Ashby, Workday) so dead boards get auto-disabled regardless of how they got
into the table.
"""

from __future__ import annotations

import concurrent.futures as cf
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx
from sqlmodel import Session, select

from job_applier.models import SourceSlug, engine
from job_applier.sources.companies import (
    ASHBY_COMPANIES,
    GREENHOUSE_COMPANIES,
    JIBE_TENANTS,
    LEVER_COMPANIES,
    ORACLE_SITES,
    SMARTRECRUITERS_COMPANIES,
    WORKABLE_COMPANIES,
    WORKDAY_BOARDS,
)
from job_applier.sources.workday import parse_slug as parse_workday_slug

log = logging.getLogger(__name__)

GH_VERIFY = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
LV_VERIFY = "https://api.lever.co/v0/postings/{slug}?mode=json"
ASHBY_VERIFY = "https://api.ashbyhq.com/posting-api/job-board/{slug}"
SR_VERIFY = "https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=1"
# Workable's verify is a POST with a body — handled by a dedicated helper.
WORKABLE_VERIFY_URL = "https://apply.workable.com/api/v3/accounts/{slug}/jobs"

# SimplifyJobs maintains community-sourced listings.json files updated via
# GitHub Actions on every PR. We pull both repos and union the slugs.
SIMPLIFY_LISTINGS = [
    "https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/dev/.github/scripts/listings.json",
    "https://raw.githubusercontent.com/SimplifyJobs/Summer2026-Internships/dev/.github/scripts/listings.json",
]
GH_URL_RE = re.compile(r"(?:boards|job-boards|boards-api)\.greenhouse\.io/([\w-]+)")
LV_URL_RE = re.compile(r"jobs\.lever\.co/([\w.-]+)")
WK_URL_RE = re.compile(r"apply\.workable\.com/([\w-]+)", re.IGNORECASE)
SR_URL_RE = re.compile(
    r"(?:jobs|careers)\.smartrecruiters\.com/([\w.-]+?)(?=/|\?|#|$)",
    re.IGNORECASE,
)
HEX_RE = re.compile(r"^[a-f0-9]{16,}$")
NUMERIC_RE = re.compile(r"^\d+$")


@dataclass
class RefreshStats:
    gh_candidates: int = 0
    lv_candidates: int = 0
    wk_candidates: int = 0
    sr_candidates: int = 0
    gh_added: int = 0
    lv_added: int = 0
    wk_added: int = 0
    sr_added: int = 0
    gh_reverified: int = 0
    lv_reverified: int = 0
    gh_disabled: int = 0
    lv_disabled: int = 0
    ashby_reverified: int = 0
    ashby_disabled: int = 0
    workday_reverified: int = 0
    workday_disabled: int = 0
    wk_reverified: int = 0
    wk_disabled: int = 0
    sr_reverified: int = 0
    sr_disabled: int = 0


_SEEDS: dict[str, list[str]] = {
    "greenhouse": GREENHOUSE_COMPANIES,
    "lever": LEVER_COMPANIES,
    "ashby": ASHBY_COMPANIES,
    "workday": WORKDAY_BOARDS,
    "workable": WORKABLE_COMPANIES,
    "smartrecruiters": SMARTRECRUITERS_COMPANIES,
    "jibe": JIBE_TENANTS,
    "oracle": ORACLE_SITES,
}


def seed_if_empty() -> int:
    """Seed each source's slugs from companies.py if that source has no rows.

    Per-source so adding a new source (e.g. Ashby) on an existing install picks
    up its seed on the next ``init`` without disturbing the populated sources.
    Returns total rows inserted across all sources.
    """
    inserted = 0
    with Session(engine()) as session:
        for source, slugs in _SEEDS.items():
            existing = session.exec(
                select(SourceSlug).where(SourceSlug.source == source).limit(1)
            ).first()
            if existing is not None:
                continue
            session.add_all(SourceSlug(source=source, slug=s) for s in slugs)
            inserted += len(slugs)
        if inserted:
            session.commit()
    return inserted


def refresh_slugs(reverify_existing: bool = False, max_workers: int = 30) -> RefreshStats:
    """Pull candidate slugs from SimplifyJobs and verify against the live APIs.

    - New verified slugs are inserted with ``enabled=True``.
    - When ``reverify_existing`` is set, every existing row is re-checked;
      rows that fail get marked disabled with ``last_error`` populated.
    - Slugs already in the DB are left alone (their enabled flag is preserved)
      unless ``reverify_existing`` is set.
    """
    stats = RefreshStats()
    gh_candidates, lv_candidates, wk_candidates, sr_candidates = (
        _fetch_candidates_from_simplify()
    )
    stats.gh_candidates = len(gh_candidates)
    stats.lv_candidates = len(lv_candidates)
    stats.wk_candidates = len(wk_candidates)
    stats.sr_candidates = len(sr_candidates)

    with Session(engine()) as session:
        existing_gh = {
            r.slug: r
            for r in session.exec(
                select(SourceSlug).where(SourceSlug.source == "greenhouse")
            ).all()
        }
        existing_lv = {
            r.slug: r
            for r in session.exec(
                select(SourceSlug).where(SourceSlug.source == "lever")
            ).all()
        }
        existing_wk = {
            r.slug: r
            for r in session.exec(
                select(SourceSlug).where(SourceSlug.source == "workable")
            ).all()
        }
        existing_sr = {
            r.slug: r
            for r in session.exec(
                select(SourceSlug).where(SourceSlug.source == "smartrecruiters")
            ).all()
        }

        new_gh = sorted(gh_candidates - set(existing_gh))
        new_lv = sorted(lv_candidates - set(existing_lv))
        new_wk = sorted(wk_candidates - set(existing_wk))
        new_sr = sorted(sr_candidates - set(existing_sr))

        gh_results = _verify_many(new_gh, GH_VERIFY, max_workers)
        lv_results = _verify_many(new_lv, LV_VERIFY, max_workers)
        wk_results = _verify_workable(new_wk, max_workers)
        sr_results = _verify_many(new_sr, SR_VERIFY, max_workers)

        now = datetime.now(timezone.utc)
        for slug, ok, count, err in gh_results:
            if ok:
                session.add(
                    SourceSlug(
                        source="greenhouse",
                        slug=slug,
                        last_fetched_at=now,
                        last_job_count=count,
                        updated_at=now,
                    )
                )
                stats.gh_added += 1

        for slug, ok, count, err in lv_results:
            if ok:
                session.add(
                    SourceSlug(
                        source="lever",
                        slug=slug,
                        last_fetched_at=now,
                        last_job_count=count,
                        updated_at=now,
                    )
                )
                stats.lv_added += 1

        # Workable accounts live forever once created — verifying just "endpoint
        # responds" floods the table with dead boards, so we only insert ones
        # that currently have at least one open posting.
        for slug, ok, count, _err in wk_results:
            if ok and (count or 0) > 0:
                session.add(
                    SourceSlug(
                        source="workable",
                        slug=slug,
                        last_fetched_at=now,
                        last_job_count=count,
                        updated_at=now,
                    )
                )
                stats.wk_added += 1

        for slug, ok, count, _err in sr_results:
            if ok and (count or 0) > 0:
                session.add(
                    SourceSlug(
                        source="smartrecruiters",
                        slug=slug,
                        last_fetched_at=now,
                        last_job_count=count,
                        updated_at=now,
                    )
                )
                stats.sr_added += 1

        if reverify_existing:
            existing_ashby = {
                r.slug: r
                for r in session.exec(
                    select(SourceSlug).where(SourceSlug.source == "ashby")
                ).all()
            }
            existing_workday = {
                r.slug: r
                for r in session.exec(
                    select(SourceSlug).where(SourceSlug.source == "workday")
                ).all()
            }

            _apply_reverify(
                rows=existing_gh,
                results=_verify_many(sorted(existing_gh), GH_VERIFY, max_workers),
                now=now,
                stats=stats,
                reverified_field="gh_reverified",
                disabled_field="gh_disabled",
            )
            _apply_reverify(
                rows=existing_lv,
                results=_verify_many(sorted(existing_lv), LV_VERIFY, max_workers),
                now=now,
                stats=stats,
                reverified_field="lv_reverified",
                disabled_field="lv_disabled",
            )
            _apply_reverify(
                rows=existing_ashby,
                results=_verify_many(sorted(existing_ashby), ASHBY_VERIFY, max_workers),
                now=now,
                stats=stats,
                reverified_field="ashby_reverified",
                disabled_field="ashby_disabled",
            )
            _apply_reverify(
                rows=existing_workday,
                results=_verify_workday(sorted(existing_workday), max_workers),
                now=now,
                stats=stats,
                reverified_field="workday_reverified",
                disabled_field="workday_disabled",
            )
            _apply_reverify(
                rows=existing_wk,
                results=_verify_workable(sorted(existing_wk), max_workers),
                now=now,
                stats=stats,
                reverified_field="wk_reverified",
                disabled_field="wk_disabled",
            )
            _apply_reverify(
                rows=existing_sr,
                results=_verify_many(sorted(existing_sr), SR_VERIFY, max_workers),
                now=now,
                stats=stats,
                reverified_field="sr_reverified",
                disabled_field="sr_disabled",
            )

        session.commit()

    return stats


def _apply_reverify(
    *,
    rows: dict[str, SourceSlug],
    results: list[tuple[str, bool, int | None, str | None]],
    now: datetime,
    stats: RefreshStats,
    reverified_field: str,
    disabled_field: str,
) -> None:
    for slug, ok, count, err in results:
        row = rows[slug]
        row.last_fetched_at = now
        row.last_job_count = count if ok else row.last_job_count
        row.last_error = None if ok else err
        if not ok and row.enabled:
            row.enabled = False
            setattr(stats, disabled_field, getattr(stats, disabled_field) + 1)
        row.updated_at = now
        setattr(stats, reverified_field, getattr(stats, reverified_field) + 1)


def _fetch_candidates_from_simplify() -> tuple[set[str], set[str], set[str], set[str]]:
    """Return (greenhouse, lever, workable, smartrecruiters) candidate slug sets.

    SmartRecruiters slugs are case-sensitive (e.g. ``Visa`` ≠ ``visa``), so
    we preserve case for that source. The other three are lowercased to keep
    dedup behaviour matching how the live APIs treat their slugs.
    """
    gh: set[str] = set()
    lv: set[str] = set()
    wk: set[str] = set()
    sr: set[str] = set()
    with httpx.Client(timeout=60.0, follow_redirects=True) as client:
        for url in SIMPLIFY_LISTINGS:
            try:
                resp = client.get(url)
                resp.raise_for_status()
                data = resp.json()
            except (httpx.HTTPError, ValueError) as e:
                log.warning("simplify listings fetch failed for %s: %s", url, e)
                continue

            for item in data:
                u = item.get("url") or ""
                for m in GH_URL_RE.finditer(u):
                    s = m.group(1).lower()
                    if not HEX_RE.match(s) and s != "embed":
                        gh.add(s)
                for m in LV_URL_RE.finditer(u):
                    s = m.group(1).lower()
                    if not HEX_RE.match(s):
                        lv.add(s)
                for m in WK_URL_RE.finditer(u):
                    s = m.group(1).lower()
                    if not NUMERIC_RE.match(s):
                        wk.add(s)
                for m in SR_URL_RE.finditer(u):
                    # SmartRecruiters URLs occasionally embed a posting ID
                    # where the company slug should be — those are numeric
                    # and useless for the company-postings endpoint.
                    s = m.group(1)
                    if not NUMERIC_RE.match(s):
                        sr.add(s)
    return gh, lv, wk, sr


def _verify_many(
    slugs: list[str], url_template: str, max_workers: int
) -> list[tuple[str, bool, int | None, str | None]]:
    if not slugs:
        return []

    def check(slug: str) -> tuple[str, bool, int | None, str | None]:
        try:
            r = httpx.get(url_template.format(slug=slug), timeout=20, follow_redirects=True)
            if r.status_code != 200:
                return (slug, False, None, f"HTTP {r.status_code}")
            payload = r.json()
            if isinstance(payload, dict):
                count = len(payload.get("jobs", []))
            elif isinstance(payload, list):
                count = len(payload)
            else:
                count = 0
            return (slug, True, count, None)
        except Exception as e:  # noqa: BLE001 — we want to capture the error string
            return (slug, False, None, str(e))

    results: list[tuple[str, bool, int | None, str | None]] = []
    with cf.ThreadPoolExecutor(max_workers=max_workers) as ex:
        for r in ex.map(check, slugs):
            results.append(r)
    return results


def _verify_workable(
    slugs: list[str], max_workers: int
) -> list[tuple[str, bool, int | None, str | None]]:
    """Workable's list endpoint is POST-only with a JSON body, so it can't share
    ``_verify_many``. Returns the response's ``total`` as the count so the
    caller can skip dead boards (total == 0)."""
    if not slugs:
        return []

    def check(slug: str) -> tuple[str, bool, int | None, str | None]:
        try:
            r = httpx.post(
                WORKABLE_VERIFY_URL.format(slug=slug),
                json={"query": ""},
                timeout=20,
                follow_redirects=True,
            )
            if r.status_code != 200:
                return (slug, False, None, f"HTTP {r.status_code}")
            payload = r.json()
            count = payload.get("total") if isinstance(payload, dict) else 0
            return (slug, True, count, None)
        except Exception as e:  # noqa: BLE001 — capture the error string
            return (slug, False, None, str(e))

    results: list[tuple[str, bool, int | None, str | None]] = []
    with cf.ThreadPoolExecutor(max_workers=max_workers) as ex:
        for r in ex.map(check, slugs):
            results.append(r)
    return results


def _verify_workday(
    slugs: list[str], max_workers: int
) -> list[tuple[str, bool, int | None, str | None]]:
    """Workday's CXS jobs endpoint is POST-only and tenant-specific, so it
    can't share ``_verify_many``. Slugs are ``tenant|region|site``.

    A 422 ("Unprocessable Entity") means the tenant rejects the public CXS
    body shape — that's a permanent rejection, not a transient error, so we
    treat it as failure and let the disable path mark the row.
    """
    if not slugs:
        return []

    def check(slug: str) -> tuple[str, bool, int | None, str | None]:
        board = parse_workday_slug(slug)
        if board is None:
            return (slug, False, None, "malformed slug")
        try:
            r = httpx.post(
                board.jobs_url,
                json={"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": ""},
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "User-Agent": "Mozilla/5.0 (compatible; job-applier/0.1)",
                },
                timeout=20,
                follow_redirects=True,
            )
            if r.status_code != 200:
                return (slug, False, None, f"HTTP {r.status_code}")
            payload = r.json()
            count = payload.get("total") if isinstance(payload, dict) else None
            return (slug, True, count, None)
        except Exception as e:  # noqa: BLE001 — capture the error string
            return (slug, False, None, str(e))

    results: list[tuple[str, bool, int | None, str | None]] = []
    with cf.ThreadPoolExecutor(max_workers=max_workers) as ex:
        for r in ex.map(check, slugs):
            results.append(r)
    return results
