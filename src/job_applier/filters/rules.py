"""Hard-rule filter applied at ingestion.

Rules (drop on any failure):
  1. Must be fully remote.
  2. Location must not be non-US-only (when a country is named).
  3. If the posting names an explicit US-state allow-list, Missouri must be in it.
  4. Title must indicate one of the configured seniority terms.
  5. Title must not be a sales / pre-sales / biz-dev role.
  6. Posting must reference one of the configured required-tech terms.
  7. A configured excluded-tech term as the primary stack disqualifies.

Ambiguous postings (e.g. tech implied via short tokens only, exclusion mentioned
in description with no positive signal) are marked `manual` so the user can
decide rather than silently dropping.

The seniority / required-tech / excluded-tech lists live on ``SearchProfile`` in
the DB and are configurable through the ``/search`` UI. If no profile row exists
the filter falls back to ``_BUILTIN_DEFAULT`` so a fresh install still filters
sanely.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

from sqlmodel import Session, select

from job_applier.models.db import FilterStatus, SearchProfile, engine

if TYPE_CHECKING:
    # Annotation-only — importing ``RawJob`` at runtime forms a cycle
    # (sources -> filters -> sources). ``from __future__ import annotations``
    # keeps the references string-evaluated at runtime.
    from job_applier.sources.base import RawJob


def _alt_pattern(terms: list[str]) -> str:
    """Regex alternation for a list of tech/seniority terms.

    Each term is escaped, internal whitespace is collapsed to ``\\s+``, and ``.``
    becomes optional (so "node.js" matches "node js" / "nodejs"). Returns an
    empty string if ``terms`` is empty so callers can short-circuit.
    """
    if not terms:
        return ""
    parts: list[str] = []
    for t in terms:
        escaped = re.escape(t.strip())
        escaped = escaped.replace(r"\ ", r"\s+").replace(r"\.", r"\.?")
        parts.append(escaped)
    return "|".join(parts)


@dataclass
class FilterConfig:
    """Compiled patterns used by ``evaluate``. Build via ``build_config`` so the
    short-vs-long classification and the Angular-style fallbacks stay consistent.
    """

    role_titles: list[str] = field(default_factory=list)
    seniority_terms: list[str] = field(default_factory=list)
    required_tech: list[str] = field(default_factory=list)
    excluded_tech: list[str] = field(default_factory=list)

    seniority_re: Optional[re.Pattern[str]] = None
    required_long_re: Optional[re.Pattern[str]] = None
    required_short_re: Optional[re.Pattern[str]] = None
    excluded_re: Optional[re.Pattern[str]] = None
    required_tags_lower: frozenset[str] = field(default_factory=frozenset)
    excluded_tags_lower: frozenset[str] = field(default_factory=frozenset)


def build_config(
    *,
    role_titles: list[str],
    seniority_terms: list[str],
    required_tech: list[str],
    excluded_tech: list[str],
) -> FilterConfig:
    seniority_re = (
        re.compile(rf"\b({_alt_pattern(seniority_terms)})\b", re.IGNORECASE)
        if seniority_terms
        else None
    )

    # Required-tech terms of 2 chars or less ("js", "ts", "go") only mark a
    # posting as "manual" when they're the sole hit — they collide too easily
    # with English to trust on their own.
    long_terms = [t for t in required_tech if len(t.strip()) > 2]
    short_terms = [t for t in required_tech if 0 < len(t.strip()) <= 2]
    required_long_re = (
        re.compile(rf"\b({_alt_pattern(long_terms)})\b", re.IGNORECASE)
        if long_terms
        else None
    )
    required_short_re = (
        re.compile(rf"\b({_alt_pattern(short_terms)})\b", re.IGNORECASE)
        if short_terms
        else None
    )

    excluded_re = (
        re.compile(rf"\b({_alt_pattern(excluded_tech)})\b", re.IGNORECASE)
        if excluded_tech
        else None
    )

    return FilterConfig(
        role_titles=list(role_titles),
        seniority_terms=list(seniority_terms),
        required_tech=list(required_tech),
        excluded_tech=list(excluded_tech),
        seniority_re=seniority_re,
        required_long_re=required_long_re,
        required_short_re=required_short_re,
        excluded_re=excluded_re,
        required_tags_lower=frozenset(t.strip().lower() for t in required_tech if t.strip()),
        excluded_tags_lower=frozenset(t.strip().lower() for t in excluded_tech if t.strip()),
    )


# Built-in defaults that match the original hardcoded behavior. Used when no
# SearchProfile exists yet (fresh install) or when the active row has empty
# lists.
_BUILTIN_DEFAULT = build_config(
    role_titles=[
        "Senior Software Engineer",
        "Staff Software Engineer",
        "Principal Software Engineer",
        "Lead Software Engineer",
    ],
    seniority_terms=[
        "senior",
        "sr",
        "staff",
        "principal",
        "lead",
        "architect",
        "distinguished",
        "head of",
        "director",
        "vp",
        "vice president",
    ],
    required_tech=[
        "javascript",
        "typescript",
        "nodejs",
        "node.js",
        "node",
        "react",
        "vue",
        "svelte",
        "nextjs",
        "next.js",
        "nuxt",
        "remix",
        "express",
        "nestjs",
        "nest.js",
        "deno",
        "bun",
        "ecmascript",
        "es6",
        "tsx",
        "jsx",
        "js",
        "ts",
    ],
    excluded_tech=["angular", "angularjs"],
)


def load_active_config(session: Optional[Session] = None) -> FilterConfig:
    """Load the active filter config from the SearchProfile row.

    Falls back to ``_BUILTIN_DEFAULT`` when no profile exists or required lists
    are empty (an empty required-tech list would drop every posting, which is
    never what the user means).
    """
    close_after = False
    if session is None:
        session = Session(engine())
        close_after = True
    try:
        profile = session.exec(select(SearchProfile).order_by(SearchProfile.id)).first()
    finally:
        if close_after:
            session.close()
    if profile is None:
        return _BUILTIN_DEFAULT
    if not profile.required_tech or not profile.seniority_terms:
        return _BUILTIN_DEFAULT
    return build_config(
        role_titles=profile.role_titles,
        seniority_terms=profile.seniority_terms,
        required_tech=profile.required_tech,
        excluded_tech=profile.excluded_tech,
    )


# Tags that name a competing frontend framework — used when deciding whether an
# excluded-tech tag (e.g. "angular") is the *primary* stack or just one of
# several. Kept intentionally narrow: language tags like "typescript" don't
# count, since a TS+Angular shop is still an Angular shop.
_COMPETING_FRAMEWORK_HINTS = frozenset(
    {"react", "vue", "svelte", "next.js", "nextjs", "nuxt", "remix", "ember", "solid"}
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


# A "City, X[, Y]" pattern. Used as a fallback signal: any specific-place
# location that lacks a US hint is treated as non-US, even if its country
# isn't enumerated in NON_US_LOCATION.
_SPECIFIC_LOCATION = re.compile(
    r"[A-Za-z][A-Za-z\s.'\-]{1,40},\s*[A-Za-z][A-Za-z\s.'\-]{1,40}"
)


def _is_specific_non_us(location: str) -> bool:
    if not location:
        return False
    if _has_us_hint(location):
        return False
    return bool(_SPECIFIC_LOCATION.search(location))


# US states + DC, full names. Used to detect explicit state allow-lists in the
# posting body. (We don't try to parse two-letter abbreviations — too many
# collisions with English words like "OR", "IN", "ME".)
US_STATES = re.compile(
    r"\b("
    r"alabama|alaska|arizona|arkansas|california|colorado|connecticut|delaware|"
    r"florida|georgia|hawaii|idaho|illinois|indiana|iowa|kansas|kentucky|"
    r"louisiana|maine|maryland|massachusetts|michigan|minnesota|mississippi|"
    r"missouri|montana|nebraska|nevada|"
    r"new\s+hampshire|new\s+jersey|new\s+mexico|new\s+york|"
    r"north\s+carolina|north\s+dakota|"
    r"ohio|oklahoma|oregon|pennsylvania|rhode\s+island|"
    r"south\s+carolina|south\s+dakota|"
    r"tennessee|texas|utah|vermont|virginia|washington|"
    r"west\s+virginia|wisconsin|wyoming|"
    r"district\s+of\s+columbia"
    r")\b",
    re.IGNORECASE,
)

# Case-insensitive on the full name; case-sensitive on the abbreviation so it
# doesn't match the prefix in words like "Monday" or "modify".
MISSOURI_TOKEN = re.compile(r"\bmissouri\b", re.IGNORECASE)
MISSOURI_ABBR = re.compile(r"\bMO\b")


def _has_missouri(text: str) -> bool:
    return bool(MISSOURI_TOKEN.search(text) or MISSOURI_ABBR.search(text))

# Phrases that announce "we only hire in these places". Each match opens a
# window we then scan for a state list.
STATE_RESTRICTION_TRIGGER = re.compile(
    r"(?:"
    r"(?:currently\s+|presently\s+|able\s+(?:only\s+)?to\s+)?hir(?:e|ing)\s+(?:employees?\s+)?in"
    r"|(?:we\s+)?employ(?:\s+employees?)?\s+in"
    r"|available\s+(?:in|to\s+(?:candidates?|applicants?)\s+(?:in|residing\s+in))"
    r"|open\s+to\s+(?:candidates?|applicants?|residents?)\s+(?:in|of|residing\s+in|based\s+in|located\s+in)"
    r"|must\s+(?:reside|live|be\s+located|be\s+based)\s+in"
    r"|residen(?:t|ce)\s+(?:in|of|required\s+in)"
    r"|approved\s+states?"
    r"|eligible\s+(?:states?|to\s+work\s+in)"
    r"|registered\s+(?:to\s+(?:employ|hire|do\s+business)\s+in|in)"
    r"|states?\s+where\s+we\s+(?:can\s+)?(?:hire|employ)"
    r"|(?:work|employ(?:ed)?)\s+from\s+(?:any\s+of\s+)?the\s+following"
    r"|(?:located|based)\s+in\s+(?:one\s+of\s+)?(?:the\s+following|these)"
    r"|this\s+role\s+is\s+(?:only\s+)?(?:open|available)\s+to\s+(?:residents?\s+of|candidates?\s+in)"
    r")",
    re.IGNORECASE,
)

# "Anywhere in the US" type phrases — if any of these appear inside a triggered
# window, the trigger is meaningless ("we hire in any US state").
NATIONWIDE_OVERRIDE = re.compile(
    r"\b(any\s+(?:US\s+)?state|all\s+(?:50\s+)?states|"
    r"any\s+state\s+in\s+the\s+(?:US|United\s+States)|"
    r"nationwide|throughout\s+the\s+(?:US|United\s+States)|"
    r"anywhere\s+in\s+the\s+(?:US|U\.S\.|United\s+States))\b",
    re.IGNORECASE,
)


def _has_state_allowlist_excluding_mo(text: str) -> bool:
    """True iff the text declares a US-state allow-list that omits Missouri.

    Strategy: each trigger-phrase match opens an 800-char scan window; if the
    window contains 1+ state names, lacks Missouri, and isn't overridden by an
    "any state" / "nationwide" phrase, it's a restriction we should drop on.
    """
    for match in STATE_RESTRICTION_TRIGGER.finditer(text):
        window = text[match.start() : match.start() + 800]
        if NATIONWIDE_OVERRIDE.search(window):
            continue
        if not US_STATES.search(window):
            continue
        if _has_missouri(window):
            continue
        return True
    return False


@dataclass
class FilterResult:
    status: FilterStatus
    reason: str | None = None


def _haystack(raw: RawJob) -> str:
    parts = [raw.title, raw.description, " ".join(raw.tags), raw.location or ""]
    return "\n".join(parts)


def title_quick_fail(title: str, config: Optional[FilterConfig] = None) -> bool:
    """Cheap title-only check — does this title fail seniority or sales rules?

    Used by adapters that fan out per-job HTTP requests (Workable,
    SmartRecruiters) to skip the expensive detail fetch when the title alone
    already disqualifies the posting. Mirrors rules 4 and 5 of ``evaluate``;
    deliberately conservative — anything that *could* pass returns ``False``.
    """
    cfg = config or _BUILTIN_DEFAULT
    title = title or ""
    if cfg.seniority_re is not None and not cfg.seniority_re.search(title):
        return True
    if SALES_TITLE.search(title) or SALES_HEAD_OF.search(title):
        return True
    return False


def evaluate(raw: RawJob, config: Optional[FilterConfig] = None) -> FilterResult:
    cfg = config or _BUILTIN_DEFAULT
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
    if _is_specific_non_us(location):
        return FilterResult(FilterStatus.dropped, "location is non-US only")

    # 3. State allow-list must include Missouri (user resides in MO)
    if _has_state_allowlist_excluding_mo(_haystack(raw)):
        return FilterResult(FilterStatus.dropped, "state allow-list excludes Missouri")

    # 4. Seniority (configurable)
    if cfg.seniority_re is not None and not cfg.seniority_re.search(title):
        return FilterResult(
            FilterStatus.dropped,
            "title not Senior/Staff/Principal/Lead (or configured equivalent)",
        )

    # 5. Sales / pre-sales / biz-dev titles
    if SALES_TITLE.search(title) or SALES_HEAD_OF.search(title):
        return FilterResult(FilterStatus.dropped, "title is sales / pre-sales / biz-dev")

    # 6. Excluded-tech check (before required-tech — an excluded term may itself
    #    satisfy required-tech, but disqualifies anyway).
    #
    #    A pure language tag (e.g. "typescript") doesn't rescue a posting tagged
    #    with the excluded tech — "angular, typescript" is still Angular-primary.
    #    We check tags against a competing-framework hint list intersected with
    #    the configured required-tech, so the user can curate which alternatives
    #    matter while we still keep the framework-vs-language distinction.
    excluded_in_title = bool(cfg.excluded_re and cfg.excluded_re.search(title))
    excluded_in_tags = bool(tags_lower & cfg.excluded_tags_lower)
    competing_frameworks_in_tags = bool(
        tags_lower
        & _COMPETING_FRAMEWORK_HINTS
        & cfg.required_tags_lower
        - cfg.excluded_tags_lower
    )
    if excluded_in_title:
        return FilterResult(FilterStatus.dropped, "excluded tech in title")
    if excluded_in_tags and not competing_frameworks_in_tags:
        return FilterResult(
            FilterStatus.dropped, "excluded tech is the listed primary stack"
        )

    # 7. Required-tech reference
    has_long = bool(cfg.required_long_re and cfg.required_long_re.search(haystack))
    has_short = bool(cfg.required_short_re and cfg.required_short_re.search(haystack))
    if cfg.required_long_re is not None or cfg.required_short_re is not None:
        if not has_long and not has_short:
            return FilterResult(
                FilterStatus.dropped,
                "no JavaScript/TypeScript (or configured required-tech) reference found",
            )
        if not has_long and has_short:
            return FilterResult(
                FilterStatus.manual,
                "only short JS/TS-style hints — verify manually",
            )

    # If an excluded term appears in description but no positive required-tech
    # signal also appears there, surface for review.
    if (
        cfg.excluded_re
        and cfg.excluded_re.search(raw.description)
        and cfg.required_long_re
        and not cfg.required_long_re.search(raw.description)
    ):
        return FilterResult(
            FilterStatus.manual, "excluded tech mentioned in description; verify primary stack"
        )

    return FilterResult(FilterStatus.passed)
