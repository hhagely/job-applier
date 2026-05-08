"""Hard-rule filter applied at ingestion.

Rules (drop on any failure):
  1. Must be fully remote.
  2. Title must indicate Senior/Staff/Principal/Lead (or equivalent).
  3. Posting must reference JavaScript/TypeScript/Node ecosystem.
  4. Angular as the primary stack disqualifies.

Ambiguous postings (e.g. JS/TS implied but not stated, seniority unclear) are
marked `manual` so the user can decide rather than silently dropping.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from job_applier.models.db import FilterStatus
from job_applier.sources.base import RawJob

SENIOR_TITLE = re.compile(
    r"\b(senior|sr\.?|staff|principal|lead|architect|distinguished|"
    r"head\s+of|director|vp|vice\s+president)\b",
    re.IGNORECASE,
)

JS_TS_TERMS = re.compile(
    r"\b(javascript|typescript|node\.?js|node|react|vue|svelte|next\.?js|nuxt|remix|"
    r"express|nest\.?js|deno|bun|ecmascript|es6|tsx|jsx)\b",
    re.IGNORECASE,
)
JS_TS_SHORT = re.compile(r"\b(js|ts)\b", re.IGNORECASE)

ANGULAR_TERM = re.compile(r"\bangular(js|\s*\d+)?\b", re.IGNORECASE)
NON_ANGULAR_FRAMEWORK = re.compile(
    r"\b(react|vue|svelte|next\.?js|nuxt|remix|ember|solid)\b",
    re.IGNORECASE,
)

ONSITE_HINTS = re.compile(
    r"\b(on[\s-]?site|hybrid|in[\s-]?office|relocat(e|ion))\b",
    re.IGNORECASE,
)


@dataclass
class FilterResult:
    status: FilterStatus
    reason: str | None = None


def _haystack(raw: RawJob) -> str:
    parts = [raw.title, raw.description, " ".join(raw.tags), raw.location or ""]
    return "\n".join(parts)


def evaluate(raw: RawJob) -> FilterResult:
    title = raw.title or ""
    haystack = _haystack(raw)
    tags_lower = {t.lower() for t in raw.tags}

    # 1. Remote
    if not raw.remote:
        return FilterResult(FilterStatus.dropped, "not remote")
    if ONSITE_HINTS.search(title) or ONSITE_HINTS.search(raw.location or ""):
        return FilterResult(FilterStatus.dropped, "title/location indicates on-site or hybrid")

    # 2. Seniority
    if not SENIOR_TITLE.search(title):
        return FilterResult(FilterStatus.dropped, "title not Senior/Staff/Principal/Lead")

    # 3. Angular check (before JS/TS — Angular IS JS/TS, but disqualifies)
    angular_in_title = bool(ANGULAR_TERM.search(title))
    angular_in_tags = "angular" in tags_lower or "angularjs" in tags_lower
    other_fw_in_tags = any(t in tags_lower for t in ("react", "vue", "svelte", "next.js", "nuxt"))
    if angular_in_title:
        return FilterResult(FilterStatus.dropped, "Angular in title")
    if angular_in_tags and not other_fw_in_tags:
        return FilterResult(FilterStatus.dropped, "Angular is the listed frontend framework")

    # 4. JS/TS reference
    has_long = bool(JS_TS_TERMS.search(haystack))
    has_short = bool(JS_TS_SHORT.search(haystack))
    if not has_long and not has_short:
        return FilterResult(FilterStatus.dropped, "no JavaScript/TypeScript reference found")
    if not has_long and has_short:
        return FilterResult(FilterStatus.manual, "only short JS/TS hints — verify manually")

    # If Angular appears in description but other frameworks too, surface for review
    if ANGULAR_TERM.search(raw.description) and not NON_ANGULAR_FRAMEWORK.search(raw.description):
        return FilterResult(FilterStatus.manual, "Angular mentioned in description; verify primary stack")

    return FilterResult(FilterStatus.passed)
