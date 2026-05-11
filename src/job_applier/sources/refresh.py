"""Slug discovery + verification for the Greenhouse / Lever ingest sources.

The runtime source of truth for which slugs to fetch is the ``SourceSlug``
table. This module fills that table — either from a small built-in seed
(used on fresh ``job-applier init``) or from the SimplifyJobs community
feed (the ``refresh-slugs`` CLI command).
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
    LEVER_COMPANIES,
    WORKDAY_BOARDS,
)

log = logging.getLogger(__name__)

GH_VERIFY = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
LV_VERIFY = "https://api.lever.co/v0/postings/{slug}?mode=json"

# SimplifyJobs maintains community-sourced listings.json files updated via
# GitHub Actions on every PR. We pull both repos and union the slugs.
SIMPLIFY_LISTINGS = [
    "https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/dev/.github/scripts/listings.json",
    "https://raw.githubusercontent.com/SimplifyJobs/Summer2026-Internships/dev/.github/scripts/listings.json",
]
GH_URL_RE = re.compile(r"(?:boards|job-boards|boards-api)\.greenhouse\.io/([\w-]+)")
LV_URL_RE = re.compile(r"jobs\.lever\.co/([\w.-]+)")
HEX_RE = re.compile(r"^[a-f0-9]{16,}$")


@dataclass
class RefreshStats:
    gh_candidates: int = 0
    lv_candidates: int = 0
    gh_added: int = 0
    lv_added: int = 0
    gh_reverified: int = 0
    lv_reverified: int = 0
    gh_disabled: int = 0
    lv_disabled: int = 0


_SEEDS: dict[str, list[str]] = {
    "greenhouse": GREENHOUSE_COMPANIES,
    "lever": LEVER_COMPANIES,
    "ashby": ASHBY_COMPANIES,
    "workday": WORKDAY_BOARDS,
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
    gh_candidates, lv_candidates = _fetch_candidates_from_simplify()
    stats.gh_candidates = len(gh_candidates)
    stats.lv_candidates = len(lv_candidates)

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

        new_gh = sorted(gh_candidates - set(existing_gh))
        new_lv = sorted(lv_candidates - set(existing_lv))

        gh_results = _verify_many(new_gh, GH_VERIFY, max_workers)
        lv_results = _verify_many(new_lv, LV_VERIFY, max_workers)

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

        if reverify_existing:
            gh_existing = sorted(existing_gh)
            lv_existing = sorted(existing_lv)
            for slug, ok, count, err in _verify_many(gh_existing, GH_VERIFY, max_workers):
                row = existing_gh[slug]
                row.last_fetched_at = now
                row.last_job_count = count if ok else row.last_job_count
                row.last_error = None if ok else err
                if not ok and row.enabled:
                    row.enabled = False
                    stats.gh_disabled += 1
                row.updated_at = now
                session.add(row)
                stats.gh_reverified += 1

            for slug, ok, count, err in _verify_many(lv_existing, LV_VERIFY, max_workers):
                row = existing_lv[slug]
                row.last_fetched_at = now
                row.last_job_count = count if ok else row.last_job_count
                row.last_error = None if ok else err
                if not ok and row.enabled:
                    row.enabled = False
                    stats.lv_disabled += 1
                row.updated_at = now
                session.add(row)
                stats.lv_reverified += 1

        session.commit()

    return stats


def _fetch_candidates_from_simplify() -> tuple[set[str], set[str]]:
    gh: set[str] = set()
    lv: set[str] = set()
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
    return gh, lv


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
