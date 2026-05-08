"""Hard-rule filter applied at ingestion.

Rules (drop on any failure):
  1. Must be fully remote.
  2. Location must not be non-US-only (when a country is named).
  3. Title must indicate Senior/Staff/Principal/Lead (or equivalent).
  4. Title must not be a sales / pre-sales / biz-dev role.
  5. Posting must reference JavaScript/TypeScript/Node ecosystem.
  6. Angular as the primary stack disqualifies.

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

# Sales / pre-sales / biz-dev titles dressed up as "Senior <X>" — drop them.
SALES_TITLE = re.compile(
    r"\b("
    r"solutions?\s+engineer|"
    r"sales\s+engineer|"
    r"account\s+(executive|manager)|"
    r"partner\s+solutions?\s+architect|"
    r"business\s+development|"
    r"customer\s+success\s+manager|"
    r"pre[-\s]?sales"
    r")\b",
    re.IGNORECASE,
)
# "Head of ..." titles where the trailing words signal a sales / biz-dev scope.
SALES_HEAD_OF = re.compile(
    r"\bhead\s+of\b.*?\b(partnerships?|sales|business\s+development|alliances?|revenue)\b",
    re.IGNORECASE,
)

# Non-US country / region / major-city tokens. If the location names one of these
# AND has no US marker, drop. "Remote", "Distributed", "Anywhere" with no country
# is left to scoring rather than filtered here.
NON_US_LOCATION = re.compile(
    r"\b("
    r"canada|mexico|brazil|argentina|chile|colombia|peru|"
    r"united\s+kingdom|england|scotland|wales|ireland|"
    r"germany|france|spain|portugal|italy|netherlands|belgium|luxembourg|"
    r"poland|romania|ukraine|austria|switzerland|"
    r"sweden|norway|denmark|finland|iceland|"
    r"czech|hungary|slovakia|bulgaria|greece|estonia|latvia|lithuania|"
    r"japan|china|korea|singapore|india|indonesia|philippines|vietnam|thailand|malaysia|"
    r"australia|new\s+zealand|"
    r"south\s+africa|nigeria|kenya|egypt|morocco|"
    r"israel|turkey|uae|saudi(\s+arabia)?|qatar|"
    r"emea|apac|latam|"
    r"london|berlin|munich|hamburg|paris|madrid|barcelona|lisbon|amsterdam|dublin|"
    r"rome|milan|warsaw|prague|stockholm|oslo|copenhagen|helsinki|"
    r"tokyo|seoul|sydney|melbourne|brisbane|"
    r"toronto|vancouver|montreal|ottawa|"
    r"sao\s+paulo|mexico\s+city|buenos\s+aires|bogot[aá]"
    r")\b",
    re.IGNORECASE,
)
# Country-name tokens are case-insensitive, but bare "US" stays case-sensitive
# so it doesn't match every appearance of the English word "us".
_US_HINT_CI = re.compile(
    r"\b(united\s+states|usa|u\.s\.a\.|u\.s\.|americas)\b",
    re.IGNORECASE,
)
_US_HINT_CS = re.compile(r"\bUS\b|\bUS[-\s]")


def _has_us_hint(location: str) -> bool:
    return bool(_US_HINT_CI.search(location) or _US_HINT_CS.search(location))


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

    # 2. US-only location (when a country/region is named)
    location = raw.location or ""
    if NON_US_LOCATION.search(location) and not _has_us_hint(location):
        return FilterResult(FilterStatus.dropped, "location is non-US only")

    # 3. Seniority
    if not SENIOR_TITLE.search(title):
        return FilterResult(FilterStatus.dropped, "title not Senior/Staff/Principal/Lead")

    # 4. Sales / pre-sales / biz-dev titles
    if SALES_TITLE.search(title) or SALES_HEAD_OF.search(title):
        return FilterResult(FilterStatus.dropped, "title is sales / pre-sales / biz-dev")

    # 5. Angular check (before JS/TS — Angular IS JS/TS, but disqualifies)
    angular_in_title = bool(ANGULAR_TERM.search(title))
    angular_in_tags = "angular" in tags_lower or "angularjs" in tags_lower
    other_fw_in_tags = any(t in tags_lower for t in ("react", "vue", "svelte", "next.js", "nuxt"))
    if angular_in_title:
        return FilterResult(FilterStatus.dropped, "Angular in title")
    if angular_in_tags and not other_fw_in_tags:
        return FilterResult(FilterStatus.dropped, "Angular is the listed frontend framework")

    # 6. JS/TS reference
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
