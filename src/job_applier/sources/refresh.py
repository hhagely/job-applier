"""Slug discovery + verification for per-company ingest sources.

The runtime source of truth for which slugs to fetch is the ``SourceSlug``
table. This module fills that table — either from a small built-in seed
(used on fresh ``job-applier init``) or from the SimplifyJobs community
feed. The feed pass is reachable two ways: the ``refresh-slugs`` CLI command
and ``POST /api/company-coverage/refresh`` (the "Update company list" button on
``/search``), which runs it as a background task via ``progress_cb``.

Discovery pulls slugs the SimplifyJobs feed carries: Greenhouse, Lever,
Workable, SmartRecruiters, and Ashby. Re-verification covers those five plus
Workday so dead boards get auto-disabled regardless of how they got into the
table. Jibe and Oracle are seed-only (neither discovered nor re-verified) —
their slugs pack fields no public URL exposes.
"""

from __future__ import annotations

import concurrent.futures as cf
import logging
import re
from collections.abc import Callable
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
from job_applier.sources.oracle import parse_slug as parse_oracle_slug
from job_applier.sources.workday import parse_slug as parse_workday_slug

log = logging.getLogger(__name__)

# Sources whose slug packs several structured fields into one delimited string
# (Workday ``tenant|region|site``, Oracle ``apiHost|siteNumber|publicBase[|company]``).
# A malformed pack makes ``parse_slug`` return None, which the adapter silently
# filters out at ingest — no board, no error. Validate on write so a bad seed
# entry surfaces as a warning instead of a slug that never ingests anything.
_PACKED_SLUG_VALIDATORS = {
    "workday": parse_workday_slug,
    "oracle": parse_oracle_slug,
}


def _valid_slugs(source: str, slugs: list[str]) -> list[str]:
    """Return the slugs that parse for ``source``, warning about any that don't.

    A no-op for sources without a packed slug format."""
    validate = _PACKED_SLUG_VALIDATORS.get(source)
    if validate is None:
        return list(slugs)
    kept: list[str] = []
    for slug in slugs:
        if validate(slug) is None:
            log.warning("skipping malformed %s slug (does not parse): %r", source, slug)
        else:
            kept.append(slug)
    return kept

GH_VERIFY = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
LV_VERIFY = "https://api.lever.co/v0/postings/{slug}?mode=json"
ASHBY_VERIFY = "https://api.ashbyhq.com/posting-api/job-board/{slug}"
SR_VERIFY = "https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=1"
JIBE_VERIFY = "https://{slug}.jibeapply.com/api/jobs?page=1"
# Workable's verify is a POST with a body — handled by a dedicated helper.
WORKABLE_VERIFY_URL = "https://apply.workable.com/api/v3/accounts/{slug}/jobs"

# Sources whose "does this board exist" check is a plain GET returning either a
# job list or a ``{"jobs": [...]}`` envelope, so they can share ``_verify_many``.
GET_VERIFY_URLS = {
    "greenhouse": GH_VERIFY,
    "lever": LV_VERIFY,
    "ashby": ASHBY_VERIFY,
    "smartrecruiters": SR_VERIFY,
    "jibe": JIBE_VERIFY,
}

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
ASHBY_URL_RE = re.compile(r"jobs\.ashbyhq\.com/([\w.-]+)", re.IGNORECASE)
HEX_RE = re.compile(r"^[a-f0-9]{16,}$")
NUMERIC_RE = re.compile(r"^\d+$")

# Sources whose slug spelling must survive discovery verbatim. SmartRecruiters
# because its API is genuinely case-sensitive (``Visa`` != ``visa``); Ashby
# because its API accepts either casing but the adapter uses the slug as the
# employer name shown in the queue (see sources/ashby.py), so lowercasing it
# would rename "Notion" to "notion" everywhere.
CASE_PRESERVING_SOURCES = {"smartrecruiters", "ashby"}

# ...and the narrower set where two spellings are provably the SAME board, so
# discovery may dedupe across casing. Only Ashby: its API returns identical
# results for ``notion`` and ``Notion``. SmartRecruiters is deliberately absent
# — its API is case-sensitive, so treating ``Visa`` and ``visa`` as one board
# could silently skip a real one.
_CASE_INSENSITIVE_DEDUPE = {"ashby"}

# Sources whose API 404s a slug it doesn't know, which makes a 200 proof that
# the board is real — even with zero open postings today. Everything else needs
# at least one posting as proof of life, for two different reasons:
# SmartRecruiters answers 200 with an empty list for ANY string (verified), so
# "it responded" means nothing there; Workable 404s properly but keeps abandoned
# accounts alive forever, so a long-squatted name still answers 200.
BOARDS_404_WHEN_MISSING = {"greenhouse", "lever", "ashby", "jibe"}


def board_exists(source: str, ok: bool, job_count: int | None) -> bool:
    """Whether a verify result proves a board worth storing.

    The single home for the per-source "is this real" rule, shared by feed
    discovery and the manual add flow so the two can't drift. Getting this wrong
    in either direction is costly: too lax floods the table with dead boards,
    too strict hides a real employer who simply isn't hiring this week.
    """
    if not ok:
        return False
    if source in BOARDS_404_WHEN_MISSING:
        return True
    return (job_count or 0) > 0


@dataclass
class RefreshStats:
    gh_candidates: int = 0
    lv_candidates: int = 0
    wk_candidates: int = 0
    sr_candidates: int = 0
    ashby_candidates: int = 0
    gh_added: int = 0
    lv_added: int = 0
    wk_added: int = 0
    sr_added: int = 0
    ashby_added: int = 0
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
            valid = _valid_slugs(source, slugs)
            session.add_all(SourceSlug(source=source, slug=s) for s in valid)
            inserted += len(valid)
        if inserted:
            session.commit()
    return inserted


# Progress steps reported through ``progress_cb``: one for the feed fetch, one per
# per-source verify pass. Reverification adds a second pass over six sources.
REFRESH_STEPS = 6
REFRESH_STEPS_REVERIFY = REFRESH_STEPS + 6


def refresh_slugs(
    reverify_existing: bool = False,
    max_workers: int = 30,
    progress_cb: "Callable[[int, int, str], None] | None" = None,
) -> RefreshStats:
    """Pull candidate slugs from SimplifyJobs and verify against the live APIs.

    - New verified slugs are inserted with ``enabled=True``.
    - When ``reverify_existing`` is set, every existing row is re-checked;
      rows that fail get marked disabled with ``last_error`` populated.
    - Slugs already in the DB are left alone (their enabled flag is preserved)
      unless ``reverify_existing`` is set.

    ``progress_cb(done, total, label)`` is called after each verify pass so a
    caller (the API's background task) can report progress; the whole run is a
    handful of long network passes, so per-pass granularity is the useful unit.
    """
    stats = RefreshStats()
    total = REFRESH_STEPS_REVERIFY if reverify_existing else REFRESH_STEPS
    done = 0

    def _step(label: str) -> None:
        nonlocal done
        done += 1
        if progress_cb is not None:
            progress_cb(done, total, label)

    candidates = _fetch_candidates_from_simplify()
    _step("fetched candidate list")
    stats.gh_candidates = len(candidates["greenhouse"])
    stats.lv_candidates = len(candidates["lever"])
    stats.wk_candidates = len(candidates["workable"])
    stats.sr_candidates = len(candidates["smartrecruiters"])
    stats.ashby_candidates = len(candidates["ashby"])

    with Session(engine()) as session:
        existing_gh = _existing_by_slug(session, "greenhouse")
        existing_lv = _existing_by_slug(session, "lever")
        existing_wk = _existing_by_slug(session, "workable")
        existing_sr = _existing_by_slug(session, "smartrecruiters")
        existing_ashby = _existing_by_slug(session, "ashby")

        new_gh = _new_slugs("greenhouse", candidates["greenhouse"], existing_gh)
        new_lv = _new_slugs("lever", candidates["lever"], existing_lv)
        new_wk = _new_slugs("workable", candidates["workable"], existing_wk)
        new_sr = _new_slugs("smartrecruiters", candidates["smartrecruiters"], existing_sr)
        new_ashby = _new_slugs("ashby", candidates["ashby"], existing_ashby)

        gh_results = _verify_many(new_gh, GH_VERIFY, max_workers)
        _step(f"checked {len(new_gh)} new Greenhouse boards")
        lv_results = _verify_many(new_lv, LV_VERIFY, max_workers)
        _step(f"checked {len(new_lv)} new Lever boards")
        wk_results = _verify_workable(new_wk, max_workers)
        _step(f"checked {len(new_wk)} new Workable boards")
        sr_results = _verify_many(new_sr, SR_VERIFY, max_workers)
        _step(f"checked {len(new_sr)} new SmartRecruiters boards")
        ashby_results = _verify_many(new_ashby, ASHBY_VERIFY, max_workers)
        _step(f"checked {len(new_ashby)} new Ashby boards")

        now = datetime.now(timezone.utc)

        def _insert_verified(source: str, results, stats_field: str) -> None:
            """Store every result that ``board_exists`` accepts for ``source``."""
            for slug, ok, count, _err in results:
                if not board_exists(source, ok, count):
                    continue
                session.add(
                    SourceSlug(
                        source=source,
                        slug=slug,
                        last_fetched_at=now,
                        last_job_count=count,
                        updated_at=now,
                    )
                )
                setattr(stats, stats_field, getattr(stats, stats_field) + 1)

        _insert_verified("greenhouse", gh_results, "gh_added")
        _insert_verified("lever", lv_results, "lv_added")
        _insert_verified("workable", wk_results, "wk_added")
        _insert_verified("smartrecruiters", sr_results, "sr_added")
        _insert_verified("ashby", ashby_results, "ashby_added")

        if reverify_existing:
            existing_workday = _existing_by_slug(session, "workday")

            _apply_reverify(
                rows=existing_gh,
                results=_verify_many(sorted(existing_gh), GH_VERIFY, max_workers),
                now=now,
                stats=stats,
                reverified_field="gh_reverified",
                disabled_field="gh_disabled",
            )
            _step(f"re-checked {len(existing_gh)} Greenhouse boards")
            _apply_reverify(
                rows=existing_lv,
                results=_verify_many(sorted(existing_lv), LV_VERIFY, max_workers),
                now=now,
                stats=stats,
                reverified_field="lv_reverified",
                disabled_field="lv_disabled",
            )
            _step(f"re-checked {len(existing_lv)} Lever boards")
            _apply_reverify(
                rows=existing_ashby,
                results=_verify_many(sorted(existing_ashby), ASHBY_VERIFY, max_workers),
                now=now,
                stats=stats,
                reverified_field="ashby_reverified",
                disabled_field="ashby_disabled",
            )
            _step(f"re-checked {len(existing_ashby)} Ashby boards")
            _apply_reverify(
                rows=existing_workday,
                results=_verify_workday(sorted(existing_workday), max_workers),
                now=now,
                stats=stats,
                reverified_field="workday_reverified",
                disabled_field="workday_disabled",
            )
            _step(f"re-checked {len(existing_workday)} Workday boards")
            _apply_reverify(
                rows=existing_wk,
                results=_verify_workable(sorted(existing_wk), max_workers),
                now=now,
                stats=stats,
                reverified_field="wk_reverified",
                disabled_field="wk_disabled",
            )
            _step(f"re-checked {len(existing_wk)} Workable boards")
            _apply_reverify(
                rows=existing_sr,
                results=_verify_many(sorted(existing_sr), SR_VERIFY, max_workers),
                now=now,
                stats=stats,
                reverified_field="sr_reverified",
                disabled_field="sr_disabled",
            )
            _step(f"re-checked {len(existing_sr)} SmartRecruiters boards")

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


def _fetch_candidates_from_simplify() -> dict[str, set[str]]:
    """Return candidate slug sets keyed by source name.

    Case handling differs by source (see ``CASE_PRESERVING_SOURCES``): SmartRecruiters
    and Ashby keep the spelling the feed carries, the rest are lowercased to
    match how their live APIs treat slugs. For the case-preserving pair the feed
    can name one company two ways (``Notion`` and ``notion``), so those collapse
    to a single candidate — otherwise we'd add the same board twice.
    """
    found: dict[str, set[str]] = {
        source: set()
        for source in ("greenhouse", "lever", "workable", "smartrecruiters", "ashby")
    }
    # For case-preserving sources: lowercase key -> chosen spelling.
    cased: dict[str, dict[str, str]] = {source: {} for source in CASE_PRESERVING_SOURCES}

    def _add_cased(source: str, slug: str) -> None:
        chosen = cased[source]
        key = slug.lower()
        # Prefer a branded spelling over an all-lowercase one, since the slug is
        # what the user ends up reading as the employer name.
        if key not in chosen or (slug != key and chosen[key] == key):
            chosen[key] = slug

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
                        found["greenhouse"].add(s)
                for m in LV_URL_RE.finditer(u):
                    s = m.group(1).lower()
                    if not HEX_RE.match(s):
                        found["lever"].add(s)
                for m in WK_URL_RE.finditer(u):
                    s = m.group(1).lower()
                    if not NUMERIC_RE.match(s):
                        found["workable"].add(s)
                for m in SR_URL_RE.finditer(u):
                    # SmartRecruiters URLs occasionally embed a posting ID
                    # where the company slug should be — those are numeric
                    # and useless for the company-postings endpoint.
                    s = m.group(1)
                    if not NUMERIC_RE.match(s):
                        _add_cased("smartrecruiters", s)
                for m in ASHBY_URL_RE.finditer(u):
                    s = m.group(1)
                    if not NUMERIC_RE.match(s) and not HEX_RE.match(s.lower()):
                        _add_cased("ashby", s)

    for source, chosen in cased.items():
        found[source] = set(chosen.values())
    return found


def _new_slugs(
    source: str, candidates: set[str], existing: dict[str, SourceSlug]
) -> list[str]:
    """Candidates not already in the table, sorted for stable progress labels.

    For ``_CASE_INSENSITIVE_DEDUPE`` sources the comparison ignores casing: the
    seed spells Ashby boards ``Notion`` while a feed URL might say ``notion``,
    and those are the same board — inserting both would double-ingest the
    company. Every other source compares exactly.
    """
    if source in _CASE_INSENSITIVE_DEDUPE:
        seen = {slug.lower() for slug in existing}
        return sorted(s for s in candidates if s.lower() not in seen)
    return sorted(candidates - set(existing))


def _existing_by_slug(session: Session, source: str) -> dict[str, SourceSlug]:
    """Map of ``slug -> row`` for every ``SourceSlug`` of ``source`` — the working
    set for discovery diffs and re-verification."""
    return {
        r.slug: r
        for r in session.exec(select(SourceSlug).where(SourceSlug.source == source)).all()
    }


# (slug, ok, job_count_or_None, error_str_or_None) — one verification outcome.
_VerifyResult = tuple[str, bool, "int | None", "str | None"]


def _run_verifier(
    slugs: list[str],
    check: Callable[[str], _VerifyResult],
    max_workers: int,
) -> list[_VerifyResult]:
    """Run ``check`` across ``slugs`` in a thread pool. Shared skeleton for the
    three per-source verifiers, which differ only in the per-slug ``check`` body.
    """
    if not slugs:
        return []
    results: list[_VerifyResult] = []
    with cf.ThreadPoolExecutor(max_workers=max_workers) as ex:
        for r in ex.map(check, slugs):
            results.append(r)
    return results


def verify_slugs(
    source: str, slugs: list[str], max_workers: int = 8, timeout: float = 20
) -> list[_VerifyResult]:
    """Check ``slugs`` against ``source``'s live API, one result per slug.

    The public entry point onto the per-source verifiers, used by the manual
    "add a company" flow (``sources.discover``) so a hand-added board is proven
    to exist before it's stored. Raises ``ValueError`` for a source with no
    verifier (Oracle — its packed slug can't be checked from a name or URL).
    """
    if not slugs:
        return []
    url_template = GET_VERIFY_URLS.get(source)
    if url_template is not None:
        return _verify_many(slugs, url_template, max_workers, timeout=timeout)
    if source == "workable":
        return _verify_workable(slugs, max_workers, timeout=timeout)
    if source == "workday":
        return _verify_workday(slugs, max_workers, timeout=timeout)
    raise ValueError(f"no board verifier for source {source!r}")


def _verify_many(
    slugs: list[str], url_template: str, max_workers: int, timeout: float = 20
) -> list[_VerifyResult]:
    def check(slug: str) -> _VerifyResult:
        try:
            r = httpx.get(
                url_template.format(slug=slug), timeout=timeout, follow_redirects=True
            )
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

    return _run_verifier(slugs, check, max_workers)


def _verify_workable(
    slugs: list[str], max_workers: int, timeout: float = 20
) -> list[_VerifyResult]:
    """Workable's list endpoint is POST-only with a JSON body, so it can't share
    ``_verify_many``'s GET ``check``. Returns the response's ``total`` as the count
    so the caller can skip dead boards (total == 0)."""

    def check(slug: str) -> _VerifyResult:
        try:
            r = httpx.post(
                WORKABLE_VERIFY_URL.format(slug=slug),
                json={"query": ""},
                timeout=timeout,
                follow_redirects=True,
            )
            if r.status_code != 200:
                return (slug, False, None, f"HTTP {r.status_code}")
            payload = r.json()
            count = payload.get("total") if isinstance(payload, dict) else 0
            return (slug, True, count, None)
        except Exception as e:  # noqa: BLE001 — capture the error string
            return (slug, False, None, str(e))

    return _run_verifier(slugs, check, max_workers)


def _verify_workday(
    slugs: list[str], max_workers: int, timeout: float = 20
) -> list[_VerifyResult]:
    """Workday's CXS jobs endpoint is POST-only and tenant-specific, so it can't
    share ``_verify_many``'s GET ``check``. Slugs are ``tenant|region|site``.

    A 422 ("Unprocessable Entity") means the tenant rejects the public CXS
    body shape — that's a permanent rejection, not a transient error, so we
    treat it as failure and let the disable path mark the row.
    """

    def check(slug: str) -> _VerifyResult:
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
                timeout=timeout,
                follow_redirects=True,
            )
            if r.status_code != 200:
                return (slug, False, None, f"HTTP {r.status_code}")
            payload = r.json()
            count = payload.get("total") if isinstance(payload, dict) else None
            return (slug, True, count, None)
        except Exception as e:  # noqa: BLE001 — capture the error string
            return (slug, False, None, str(e))

    return _run_verifier(slugs, check, max_workers)
