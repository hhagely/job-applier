"""Resolve one company to the ATS board(s) we can watch for it.

Powers the "add a company" box on ``/search``: the user types a company name or
pastes a careers-page URL, and this module turns that into concrete
``(source, slug)`` pairs — each one checked against the live API before the
caller stores it as a ``SourceSlug`` row. That's the difference from
``refresh.py``, which discovers boards in bulk from the SimplifyJobs feed; here
the user names a single employer they care about.

Two ways in:

- **A board URL** (``parse_board_url``) — exact, and the only way to reach
  Workday, whose slug packs ``tenant|region|site``.
- **A company name** (``probe_company``) — we derive slug candidates from the
  name and probe every source that can be checked from a bare slug. This is a
  guess by construction, so a name match must return at least one open posting;
  a URL the user pasted deliberately only has to respond.

Oracle is URL-and-name-unreachable (its slug packs an internal Fusion API host
and a numeric site id that no public URL exposes), so it stays seed-only.
"""

from __future__ import annotations

import concurrent.futures as cf
import re
from dataclasses import dataclass
from urllib.parse import urlparse

from job_applier.dedupe import normalize_company
from job_applier.sources.refresh import (
    CASE_PRESERVING_SOURCES,
    board_exists,
    verify_slugs,
)

# Sources reachable from a bare company name. Order is preference order: when a
# name matches boards on more than one ATS, this decides which is reported first.
PROBE_SOURCES = ["greenhouse", "lever", "ashby", "smartrecruiters", "workable"]

# Human-facing names for the sources a user can add by hand, used in the
# "paste a careers URL" hint when a name probe comes up empty.
SUPPORTED_LABELS = {
    "greenhouse": "Greenhouse",
    "lever": "Lever",
    "ashby": "Ashby",
    "smartrecruiters": "SmartRecruiters",
    "workable": "Workable",
    "workday": "Workday",
    "jibe": "Jibe",
}

_WORD = re.compile(r"[A-Za-z0-9]+")
# Workday paths carry a locale segment ahead of the site (``/en-US/Careers``).
_LOCALE = re.compile(r"^[a-z]{2}([-_][A-Za-z]{2})?$")


@dataclass(frozen=True)
class Board:
    """One watchable board: a ``SourceSlug`` row waiting to be written.

    ``job_count`` is what the verify call saw (None when the API doesn't report
    a total) — surfaced in the UI so the user can tell a live board from a
    dormant one.
    """

    source: str
    slug: str
    job_count: int | None = None


def parse_board_url(text: str) -> Board | None:
    """Extract the source + slug from a pasted job-board URL, or None.

    Returns None for anything unrecognized — including non-URL text, which is
    the signal for the caller to fall back to a name probe.
    """
    raw = (text or "").strip()
    if not raw:
        return None
    # urlparse needs a scheme to populate netloc; users paste bare hosts too.
    parsed = urlparse(raw if "//" in raw else f"https://{raw}")
    host = (parsed.netloc or "").lower().split(":")[0]
    if not host or "." not in host:
        return None
    parts = [p for p in (parsed.path or "").split("/") if p]

    if host.endswith("greenhouse.io"):
        # boards.greenhouse.io/acme, job-boards.greenhouse.io/acme/jobs/123,
        # boards-api.greenhouse.io/v1/boards/acme/jobs
        if parts[:2] == ["v1", "boards"] and len(parts) >= 3:
            return Board("greenhouse", parts[2].lower())
        if parts and parts[0] == "embed":
            # boards.greenhouse.io/embed/job_board?for=acme
            for pair in (parsed.query or "").split("&"):
                key, _, value = pair.partition("=")
                if key == "for" and value:
                    return Board("greenhouse", value.lower())
            return None
        return Board("greenhouse", parts[0].lower()) if parts else None

    if host.endswith("lever.co") and parts:
        return Board("lever", parts[0].lower())

    if host.endswith("ashbyhq.com") and parts:
        # Case kept: the Ashby API accepts either spelling, but the adapter uses
        # the slug as the employer name in the queue.
        return Board("ashby", parts[0])

    if host.endswith("workable.com") and parts:
        return Board("workable", parts[0].lower())

    if host.endswith("smartrecruiters.com") and parts:
        # SmartRecruiters slugs are case-sensitive (``Visa`` != ``visa``), so
        # the path segment is kept verbatim.
        return Board("smartrecruiters", parts[0])

    if host.endswith("jibeapply.com"):
        tenant = host.split(".")[0]
        return Board("jibe", tenant) if tenant else None

    if host.endswith("myworkdayjobs.com"):
        return _parse_workday_url(host, parts)

    return None


def _parse_workday_url(host: str, parts: list[str]) -> Board | None:
    """``{tenant}.{region}.myworkdayjobs.com/[locale/]{site}`` -> packed slug.

    Also accepts the CXS API form the adapter itself calls,
    ``/wday/cxs/{tenant}/{site}/jobs``, since that's what shows up in a browser
    network tab.
    """
    labels = host.split(".")
    if len(labels) < 4:  # tenant.region.myworkdayjobs.com
        return None
    tenant, region = labels[0], labels[1]
    if parts[:2] == ["wday", "cxs"] and len(parts) >= 4:
        return Board("workday", f"{parts[2]}|{region}|{parts[3]}")
    site = next((p for p in parts if not _LOCALE.match(p)), None)
    if not site:
        return None
    return Board("workday", f"{tenant}|{region}|{site}")


def company_keys(name: str) -> set[str]:
    """Every key a company name might be stored under in ``SourceSlug.slug``.

    Slugs spell the same company several ways (``acmecorp``, ``acme-corp``,
    ``acme``), and ``normalize_company`` strips a trailing legal suffix only
    when it's a separate word — so "Acme Corp" collapses to ``acme`` while the
    slug ``acme-corp`` collapses to ``acmecorp``. Returning both forms lets the
    caller match a typed name against a stored slug from either direction.
    """
    words = _WORD.findall(name or "")
    keys = {"".join(words).lower(), normalize_company(name)}
    return {k for k in keys if k}


def slug_candidates(name: str, *, preserve_case: bool = False) -> list[str]:
    """Slug spellings to probe for a typed company name, most likely first.

    ``preserve_case`` is for the sources in ``CASE_PRESERVING_SOURCES``, whose
    slugs mirror the brand's own capitalization (``Visa``, ``Notion``) — because
    SmartRecruiters' API is case-sensitive, and because Ashby's slug is what the
    user reads as the employer name.
    """
    words = _WORD.findall(name or "")
    if not words:
        return []
    joined = "".join(words)
    hyphenated = "-".join(words)
    stripped = normalize_company(name)
    out: list[str] = []
    for candidate in (joined, hyphenated, stripped):
        if not preserve_case:
            candidate = candidate.lower()
        if candidate and candidate not in out:
            out.append(candidate)
    return out


def probe_company(name: str, max_workers: int = 8, timeout: float = 10) -> list[Board]:
    """Find live boards for a typed company name across every probe-able source.

    Sources are checked concurrently (each verifier fans out over its own
    candidates), so the wall clock is one slow API rather than the sum of five.

    What counts as a hit is ``board_exists``, the same per-source rule feed
    discovery uses. On Greenhouse/Lever/Ashby a 200 is proof, so a company with
    no openings today still resolves — which is the point when the user names an
    employer they want watched. SmartRecruiters (200s for any string) and
    Workable (keeps abandoned accounts) need a live posting instead.
    """
    if not slug_candidates(name):
        return []

    def check(source: str) -> list[Board]:
        candidates = slug_candidates(
            name, preserve_case=source in CASE_PRESERVING_SOURCES
        )
        results = verify_slugs(source, candidates, max_workers=len(candidates), timeout=timeout)
        return [
            Board(source, slug, count)
            for slug, ok, count, _err in results
            if board_exists(source, ok, count)
        ]

    found: dict[str, list[Board]] = {}
    with cf.ThreadPoolExecutor(max_workers=max_workers) as ex:
        for source, boards in zip(PROBE_SOURCES, ex.map(check, PROBE_SOURCES)):
            if boards:
                # One board per source: the candidates are spellings of the same
                # company, so the first (most literal) hit is the one to keep.
                found[source] = boards
    return [found[source][0] for source in PROBE_SOURCES if source in found]


def verify_board(board: Board, timeout: float = 15) -> tuple[bool, int | None, str | None]:
    """Check a single board resolved from a pasted URL.

    Returns ``(exists, job_count, reason)`` where ``reason`` is a phrase naming
    why it wasn't accepted. A deliberate paste still has to clear
    ``board_exists``: SmartRecruiters answers 200 for any string, so without
    that check a typo'd SR URL would be stored as a permanently empty board.
    """
    try:
        results = verify_slugs(board.source, [board.slug], max_workers=1, timeout=timeout)
    except ValueError as exc:  # source with no verifier
        return (False, None, str(exc))
    if not results:
        return (False, None, "could not be checked")
    _slug, ok, count, err = results[0]
    if board_exists(board.source, ok, count):
        return (True, count, None)
    if ok:
        return (
            False,
            count,
            "responded but has no open postings, so it can't be confirmed as a "
            "real board — try again when they're hiring",
        )
    return (False, count, f"didn't respond ({err or 'no response'})")
