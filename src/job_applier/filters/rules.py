"""Hard-rule filter, in two stages.

Shared rules (``evaluate_shared``, run by ingest for everyone; a failure means
the posting is never stored, and they never return ``manual``):
  1. Must be fully remote, or hybrid / on-site in a city a searched country
     allows (``Market.hybrid_cities``).
  2. Must be workable from a searched country: the US, plus the countries any
     profile added (``SearchProfile.markets``; ingest uses ``union_markets``).
  3. Title must not be a sales / pre-sales / biz-dev role.
  4. Posting must not be a crypto / blockchain / web3 role.

Per-profile rules (``evaluate_profile``, run by ``matching`` for each profile
over the stored postings; the verdict lands on that profile's JobProfileLink):
  4b. Rules 1-2 again with this profile's own countries; a location naming
      only a region that covers one ("Remote - EMEA") is ``manual``.
  4c. A posting that won't sponsor a visa is dropped when every country it's
      workable from (for this profile) is one the profile needs sponsorship in.
  5. If the posting names an explicit US-state allow-list, the profile's home
     state must be in it. Skipped when no home state is configured.
  6. Title must indicate one of the configured seniority terms.
  6b. Title must contain one of the configured title keywords (the job
      function, e.g. "project manager"). Skipped when none are configured.
  7. Posting must reference one of the configured required-tech terms.
  8. A configured excluded-tech term as the primary stack disqualifies.

``evaluate`` composes the two.

Ambiguous postings (e.g. tech implied via short tokens only, exclusion mentioned
in description with no positive signal) are marked `manual` so the user can
decide rather than silently dropping.

The title-keyword / seniority / required-tech / excluded-tech lists and the home
state live on ``SearchProfile`` in the DB and are configurable through the
``/search`` UI. Each profile is filtered by its own lists; only a profile with no
criteria at all (or no profile row) falls back to ``_BUILTIN_DEFAULT`` so a fresh
install still filters sanely. The home state is deliberately *not* part of the built-in
default — an unset home state skips the state-allow-list rule rather than assuming
any particular state, so the tool is usable by anyone regardless of where they live.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from typing import Optional

from job_applier.contracts import RawJob
from job_applier.countries import (
    COUNTRY_BY_CODE,
    REGIONS,
    US,
    US_MARKET,
    Market,
    markets_from_rows,
    normalize_country,
    text_countries,
    text_regions,
)
from job_applier.models.db import FilterStatus, SearchProfile


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


# Canonical US state (+ DC) list — the single source of truth for the home-state
# normalizer and the abbreviation lookup used by the allow-list rule. Proper-cased
# full names are what we store on ``SearchProfile.home_state``.
US_STATE_CHOICES: tuple[tuple[str, str], ...] = (
    ("Alabama", "AL"), ("Alaska", "AK"), ("Arizona", "AZ"), ("Arkansas", "AR"),
    ("California", "CA"), ("Colorado", "CO"), ("Connecticut", "CT"), ("Delaware", "DE"),
    ("Florida", "FL"), ("Georgia", "GA"), ("Hawaii", "HI"), ("Idaho", "ID"),
    ("Illinois", "IL"), ("Indiana", "IN"), ("Iowa", "IA"), ("Kansas", "KS"),
    ("Kentucky", "KY"), ("Louisiana", "LA"), ("Maine", "ME"), ("Maryland", "MD"),
    ("Massachusetts", "MA"), ("Michigan", "MI"), ("Minnesota", "MN"), ("Mississippi", "MS"),
    ("Missouri", "MO"), ("Montana", "MT"), ("Nebraska", "NE"), ("Nevada", "NV"),
    ("New Hampshire", "NH"), ("New Jersey", "NJ"), ("New Mexico", "NM"), ("New York", "NY"),
    ("North Carolina", "NC"), ("North Dakota", "ND"), ("Ohio", "OH"), ("Oklahoma", "OK"),
    ("Oregon", "OR"), ("Pennsylvania", "PA"), ("Rhode Island", "RI"), ("South Carolina", "SC"),
    ("South Dakota", "SD"), ("Tennessee", "TN"), ("Texas", "TX"), ("Utah", "UT"),
    ("Vermont", "VT"), ("Virginia", "VA"), ("Washington", "WA"), ("West Virginia", "WV"),
    ("Wisconsin", "WI"), ("Wyoming", "WY"), ("District of Columbia", "DC"),
)

STATE_ABBREV_BY_NAME: dict[str, str] = {name: abbr for name, abbr in US_STATE_CHOICES}
# Both the lowercased full name and the lowercased abbreviation resolve to the
# canonical proper name, so "mo", "MO", "missouri", and "Missouri" all normalize.
_STATE_BY_KEY: dict[str, str] = {}
for _name, _abbr in US_STATE_CHOICES:
    _STATE_BY_KEY[_name.lower()] = _name
    _STATE_BY_KEY[_abbr.lower()] = _name


def normalize_home_state(raw: Optional[str]) -> Optional[str]:
    """Normalize user-entered state input to its canonical proper name.

    Accepts a full name or two-letter code, case-insensitively. Returns ``None``
    for blank/empty input (meaning "no home state — skip the rule"), and raises
    ``ValueError`` for a non-blank value that isn't a US state or DC so the API
    can reject it rather than silently storing something the filter can't match.
    """
    if raw is None or not raw.strip():
        return None
    name = _STATE_BY_KEY.get(raw.strip().lower())
    if name is None:
        raise ValueError(f"unrecognized US state: {raw!r}")
    return name


def _canonical_state(raw: Optional[str]) -> Optional[str]:
    """Best-effort canonicalization used at config-build time. Unlike
    ``normalize_home_state`` this never raises — an unrecognized stored value is
    returned as-is (so full-name matching can still work) rather than crashing the
    ingest filter on a legacy/odd row. The API is the validation choke point."""
    if raw is None or not raw.strip():
        return None
    return _STATE_BY_KEY.get(raw.strip().lower(), raw.strip())


@dataclass
class FilterConfig:
    """Compiled patterns used by ``evaluate``. Build via ``build_config`` so the
    short-vs-long classification and the Angular-style fallbacks stay consistent.
    """

    role_titles: list[str] = field(default_factory=list)
    title_terms: list[str] = field(default_factory=list)
    seniority_terms: list[str] = field(default_factory=list)
    required_tech: list[str] = field(default_factory=list)
    excluded_tech: list[str] = field(default_factory=list)
    # Canonical full name of the user's state of residence (e.g. "Missouri"), or
    # ``None`` to skip the state-allow-list rule entirely. ``home_state_abbr`` is
    # the derived two-letter code ("MO") used for abbreviation matching.
    home_state: Optional[str] = None
    home_state_abbr: Optional[str] = None
    # Countries the posting may be done from: the US first, then any the profile
    # added (``countries.markets_from_rows``).
    markets: tuple[Market, ...] = (US_MARKET,)

    seniority_re: Optional[re.Pattern[str]] = None
    title_re: Optional[re.Pattern[str]] = None
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
    title_terms: Optional[list[str]] = None,
    home_state: Optional[str] = None,
    markets: tuple[Market, ...] = (US_MARKET,),
) -> FilterConfig:
    seniority_re = (
        re.compile(rf"\b({_alt_pattern(seniority_terms)})\b", re.IGNORECASE)
        if seniority_terms
        else None
    )
    title_terms = [t for t in (title_terms or []) if t.strip()]
    title_re = (
        re.compile(rf"\b({_alt_pattern(title_terms)})\b", re.IGNORECASE)
        if title_terms
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

    canonical_state = _canonical_state(home_state)
    return FilterConfig(
        role_titles=list(role_titles),
        title_terms=title_terms,
        seniority_terms=list(seniority_terms),
        required_tech=list(required_tech),
        excluded_tech=list(excluded_tech),
        home_state=canonical_state,
        home_state_abbr=STATE_ABBREV_BY_NAME.get(canonical_state) if canonical_state else None,
        markets=markets,
        seniority_re=seniority_re,
        title_re=title_re,
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


def has_criteria(profile: SearchProfile) -> bool:
    """Whether the profile describes a search of its own. Any one gating list is
    enough: a project manager may set title keywords and seniority with no tech
    at all, and must then be filtered by those, not by the built-in engineering
    defaults. An empty list simply skips its rule."""
    return bool(profile.title_terms or profile.seniority_terms or profile.required_tech)


def config_for(profile: Optional[SearchProfile]) -> FilterConfig:
    """The filter config one profile's row describes (built-in defaults when
    there's no row). ``matching`` builds one of these per profile."""
    if profile is None:
        return _BUILTIN_DEFAULT
    markets = markets_from_rows(profile.markets)
    if not has_criteria(profile):
        # Fall back to the built-in role/tech defaults, but still honor the
        # profile's home state and countries. Neither depends on the tech lists,
        # and the onboarding wizard sets the state *before* any roles exist, so
        # a fresh profile (empty lists) must not silently drop the state the
        # user just chose.
        canonical = _canonical_state(profile.home_state)
        if canonical is None and markets == _BUILTIN_DEFAULT.markets:
            return _BUILTIN_DEFAULT
        return replace(
            _BUILTIN_DEFAULT,
            home_state=canonical,
            home_state_abbr=STATE_ABBREV_BY_NAME.get(canonical) if canonical else None,
            markets=markets,
        )
    return build_config(
        role_titles=profile.role_titles,
        title_terms=profile.title_terms or [],
        seniority_terms=profile.seniority_terms,
        required_tech=profile.required_tech,
        excluded_tech=profile.excluded_tech,
        home_state=profile.home_state,
        markets=markets,
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

# Cryptocurrency / blockchain / web3 industry signals. The user doesn't want
# roles from that world, so any of these in the title/description/tags drops the
# posting. Terms are kept deliberately unambiguous: bare "crypto" is omitted (it
# collides with "cryptography"), and "DAO" is omitted (collides with the Java
# Data-Access-Object pattern). "crypto" only counts when glued to a finance
# token (crypto-currency, crypto exchange, crypto wallet, ...).
CRYPTO_BLOCKCHAIN = re.compile(
    r"\b("
    r"blockchain|"
    r"cryptocurrenc(?:y|ies)|"
    r"crypto[\s-]?(?:currenc(?:y|ies)|exchange|wallet|trading|token|asset|native)|"
    r"web\s?3(?:\.0)?|"
    r"defi|"
    r"nfts?|"
    r"ethereum|"
    r"bitcoin|"
    r"solidity|"
    r"stablecoins?|"
    r"dapps?|"
    r"on[\s-]?chain|"
    r"smart\s+contracts?|"
    r"proof[\s-]of[\s-](?:stake|work)"
    r")\b",
    re.IGNORECASE,
)

# Country-name tokens are case-insensitive, but bare "US" stays case-sensitive
# so it doesn't match every appearance of the English word "us".
_US_HINT_CI = re.compile(
    r"\b(united\s+states|usa|u\.s\.a\.|u\.s\.|americas|north\s+america)\b",
    re.IGNORECASE,
)
_US_HINT_CS = re.compile(r"\bUS\b|\bUS[-\s]")

# A comma-preceded 2-letter US-state code ("Austin, TX") is a strong US signal in
# a location field, so it shouldn't be dropped just because the string omits an
# explicit country. Case-sensitive (uppercase) + comma-anchored so it can't fire
# on lowercase English words ("in"/"or"/"me") the way a bare \bST\b would. The set
# is disjoint from Canadian province codes (ON/BC/AB/...), so "Toronto, ON" is
# still correctly treated as non-US. Full state *names* are handled separately by
# the state allow-list rule below.
_US_STATE_ABBREVS = (
    "AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS "
    "MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV "
    "WI WY DC"
).split()
_US_STATE_ABBREV = re.compile(r",\s*(" + "|".join(_US_STATE_ABBREVS) + r")\b")
# The whole "City, ST" — a US town, whatever foreign city shares its name.
_US_CITY_STATE = re.compile(
    r"[A-Za-z][A-Za-z.'\- ]*,\s*(?:" + "|".join(_US_STATE_ABBREVS) + r")\b"
)


def _has_us_hint(location: str) -> bool:
    return bool(
        _US_HINT_CI.search(location)
        or _US_HINT_CS.search(location)
        or _US_STATE_ABBREV.search(location)
    )


# A "City, X[, Y]" pattern. A specific place the catalog can't name and that
# carries no US hint is treated as somewhere outside every searchable country.
_SPECIFIC_LOCATION = re.compile(
    r"[A-Za-z][A-Za-z\s.'\-]{1,40},\s*[A-Za-z][A-Za-z\s.'\-]{1,40}"
)


# US states + DC, full names. Used to detect explicit state allow-lists in the
# posting body. Derived from the canonical ``US_STATE_CHOICES`` above so the two
# can't drift; internal spaces match any whitespace run ("New York" / "New  York").
# (We don't try to parse two-letter abbreviations here — too many collisions with
# English words like "OR", "IN", "ME".)
US_STATES = re.compile(
    r"\b(" + "|".join(re.escape(n).replace(r"\ ", r"\s+") for n, _ in US_STATE_CHOICES) + r")\b",
    re.IGNORECASE,
)

def _text_has_state(text: str, name: str, abbr: Optional[str]) -> bool:
    """True if ``text`` names the given state by full name (case-insensitive) or by
    its two-letter code (case-sensitive, so "MO" doesn't fire on "Monday"/"modify").
    Internal whitespace in a multi-word name matches any run of whitespace."""
    name_pat = re.escape(name).replace(r"\ ", r"\s+")
    if re.search(rf"\b{name_pat}\b", text, re.IGNORECASE):
        return True
    if abbr and re.search(rf"\b{re.escape(abbr)}\b", text):
        return True
    return False

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


def _has_state_allowlist_excluding_home(text: str, cfg: FilterConfig) -> bool:
    """True iff the text declares a US-state allow-list that omits the user's home state.

    Strategy: each trigger-phrase match opens an 800-char scan window; if the
    window contains 1+ state names, lacks the home state, and isn't overridden by an
    "any state" / "nationwide" phrase, it's a restriction we should drop on. Callers
    must only invoke this when ``cfg.home_state`` is set (an unset home state means
    the rule does not apply).
    """
    name = cfg.home_state
    if not name:
        return False
    abbr = cfg.home_state_abbr
    for match in STATE_RESTRICTION_TRIGGER.finditer(text):
        window = text[match.start() : match.start() + 800]
        if NATIONWIDE_OVERRIDE.search(window):
            continue
        if not US_STATES.search(window):
            continue
        if _text_has_state(window, name, abbr):
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


# A posting saying it won't sponsor a work visa, or that the hire must already
# have the right to work. Only consulted for a country the profile needs
# sponsorship in, so a US "must be authorized without sponsorship" line never
# costs a profile that searches the US.
NO_SPONSORSHIP = re.compile(
    r"\b(?:"
    r"(?:unable|not\s+able|cannot|can\s*not|can't|won't|will\s+not|do\s+not|don't|does\s+not|doesn't)"
    r"\s+(?:to\s+)?(?:currently\s+)?(?:offer\s+|provide\s+|support\s+)?(?:visa\s+)?sponsor"
    r"|no\s+(?:visa\s+)?sponsorship"
    r"|sponsorship\s+(?:is\s+)?(?:not\s+|un)available"
    r"|without\s+(?:the\s+need\s+for\s+)?(?:current\s+or\s+future\s+)?(?:visa\s+)?sponsorship"
    r"|(?:must|need\s+to|required\s+to)\s+(?:already\s+)?(?:have|hold|possess)\s+"
    r"(?:the\s+|an?\s+)?(?:existing\s+|full\s+|valid\s+)?right\s+to\s+work"
    r")",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class _Where:
    """Whether a posting can be done from one of a profile's countries.

    ``markets`` are the profile's markets it can be done from (all of them for a
    location that names nowhere, like "Remote"), which the sponsorship rule
    reads; ``reason`` explains a drop or a ``manual``.
    """

    status: FilterStatus
    reason: Optional[str] = None
    markets: tuple[Market, ...] = ()


def _place(raw: RawJob) -> tuple[set[str], set[str], bool]:
    """Where the posting says it can be done: (countries, region countries,
    unknown-foreign).

    When the source sends structured countries they decide, plus the US if the
    location text names it (a Lever role filed under Canada with a "Remote - US"
    location; "Bengaluru-HQ" under India stays India). Only a source with no
    structured country is placed from the text, where a "City, ST" US town is
    taken out first so "Edinburgh, TX" isn't read as Scotland. A region word
    ("EMEA") only counts when no country is named, and a "City, X" place the
    catalog can't name, with no US hint, is somewhere outside every searchable
    country.
    """
    location = raw.location or ""
    us = {US} if _has_us_hint(location) else set()
    structured = {c for c in map(normalize_country, raw.countries) if c}
    if structured:
        countries = structured | us
    else:
        countries = text_countries(_US_CITY_STATE.sub(" ", location)) | us
    regions: set[str] = set()
    if not countries:
        for word in text_regions(location):
            regions |= REGIONS[word]
    unknown = not countries and not regions and bool(_SPECIFIC_LOCATION.search(location))
    return countries, regions, unknown


def _city_named(location: str, city: str) -> bool:
    return bool(re.search(rf"\b{_alt_pattern([city])}\b", location, re.IGNORECASE))


def _where(raw: RawJob, markets: tuple[Market, ...]) -> _Where:
    """Rules 1-2: the posting must be workable from one of ``markets``: fully
    remote, or hybrid / on-site in a city a market allows, and located in one of
    the markets' countries. A location naming only a region that covers one of
    them ("Remote - EMEA" for a UK search) is ``manual``; the user decides."""
    location = raw.location or ""
    countries, regions, unknown = _place(raw)
    by_code = {m.country: m for m in markets}

    if countries:
        accepting = tuple(by_code[c] for c in countries if c in by_code)
    elif regions:
        accepting = tuple(by_code[c] for c in sorted(regions) if c in by_code)
    elif unknown:
        accepting = ()
    else:
        accepting = markets  # names nowhere ("Remote"): left to scoring

    onsite = not raw.remote or bool(
        ONSITE_HINTS.search(raw.title or "") or ONSITE_HINTS.search(location)
    )
    if onsite:
        # Hybrid / on-site is fine only in a city a market names, for a posting
        # placed in that market's country or in no catalog country at all (a
        # town the catalog doesn't know, like "Livingston, West Lothian").
        candidates = () if regions else (accepting if countries else markets)
        accepting = tuple(
            m for m in candidates if any(_city_named(location, c) for c in m.hybrid_cities)
        )
        if not accepting:
            if not raw.remote:
                return _Where(FilterStatus.dropped, "not remote")
            return _Where(FilterStatus.dropped, "title/location indicates on-site or hybrid")

    if not accepting:
        if len(markets) == 1 and markets[0].country == US:
            return _Where(FilterStatus.dropped, "location is non-US only")
        return _Where(FilterStatus.dropped, "location is outside the countries you search")

    if regions:
        names = ", ".join(COUNTRY_BY_CODE[m.country].name for m in accepting)
        return _Where(
            FilterStatus.manual,
            f"region-wide location ({location.strip()}) — check it hires in {names}",
            accepting,
        )
    return _Where(FilterStatus.passed, None, accepting)


def union_markets(configs: list[FilterConfig]) -> tuple[Market, ...]:
    """Markets that admit a posting any of ``configs``' markets would, for the
    scrape: it serves every profile at once, so it may only drop what every
    profile would. Each country's hybrid cities are unioned; sponsorship is a
    per-profile rule and doesn't apply here."""
    cities: dict[str, list[str]] = {US: []}
    for cfg in configs:
        for m in cfg.markets:
            merged = cities.setdefault(m.country, [])
            merged.extend(c for c in m.hybrid_cities if c not in merged)
    return tuple(Market(code, hybrid_cities=tuple(cs)) for code, cs in cities.items())


def union_title_config(configs: list[FilterConfig]) -> FilterConfig:
    """A config whose title gates pass a title any of ``configs`` would.

    For the title pre-skip in adapters: the scrape serves every profile at once,
    so a title may only be skipped when no profile's terms match it. Each gate
    (seniority, title keywords) is unioned on its own, and any profile without
    that gate disables it. That is looser than "some profile passes both", never
    stricter, so it can only fetch extra details, not lose a posting.
    """
    if not configs:
        return _BUILTIN_DEFAULT

    def _union(patterns: list[Optional[re.Pattern[str]]]) -> Optional[re.Pattern[str]]:
        if any(p is None for p in patterns):
            return None
        return re.compile("|".join(f"(?:{p.pattern})" for p in patterns), re.IGNORECASE)

    return replace(
        _BUILTIN_DEFAULT,
        seniority_re=_union([c.seniority_re for c in configs]),
        title_re=_union([c.title_re for c in configs]),
    )


def title_quick_fail(title: str, config: Optional[FilterConfig] = None) -> bool:
    """Cheap title-only check — does this title fail the seniority, title-keyword,
    or sales rules?

    Used by adapters that fan out per-job HTTP requests (Workable,
    SmartRecruiters) to skip the expensive detail fetch when the title alone
    already disqualifies the posting. Mirrors the seniority, title-keyword, and
    sales rules; deliberately conservative — anything that *could* pass returns
    ``False``.
    With several profiles, pass ``union_title_config(...)`` so a title is only
    skipped when it fails *every* profile.
    """
    cfg = config or _BUILTIN_DEFAULT
    title = title or ""
    if cfg.seniority_re is not None and not cfg.seniority_re.search(title):
        return True
    if cfg.title_re is not None and not cfg.title_re.search(title):
        return True
    if SALES_TITLE.search(title) or SALES_HEAD_OF.search(title):
        return True
    return False


def evaluate(raw: RawJob, config: Optional[FilterConfig] = None) -> FilterResult:
    """The full hard filter for one profile: the shared rules, then the profile's.

    Ingest no longer calls this directly (it stores what passes
    ``evaluate_shared`` and ``matching`` applies ``evaluate_profile`` per
    profile); it remains the single-profile composition that ``diagnose-filter``
    and the tests reason about.
    """
    cfg = config or _BUILTIN_DEFAULT
    shared = evaluate_shared(raw, cfg.markets)
    if shared.status is FilterStatus.dropped:
        return shared
    return evaluate_profile(raw, cfg)


def evaluate_shared(
    raw: RawJob, markets: tuple[Market, ...] = (US_MARKET,)
) -> FilterResult:
    """Rules that are the same for every profile: workable from one of
    ``markets`` (remote, or hybrid in a city a market allows), no sales titles,
    no crypto. Ingest passes ``union_markets`` of every profile, so a posting is
    stored when any profile could want it; one that fails is never stored.
    Never returns ``manual`` (a region-wide location passes here and is left to
    ``evaluate_profile``)."""
    title = raw.title or ""

    # 1-2. Remote (or an allowed hybrid city), in one of the searched countries.
    where = _where(raw, markets)
    if where.status is FilterStatus.dropped:
        return FilterResult(FilterStatus.dropped, where.reason)

    # 3. Sales / pre-sales / biz-dev titles
    if SALES_TITLE.search(title) or SALES_HEAD_OF.search(title):
        return FilterResult(FilterStatus.dropped, "title is sales / pre-sales / biz-dev")

    # 4. Crypto / blockchain / web3 industry
    if CRYPTO_BLOCKCHAIN.search(_haystack(raw)):
        return FilterResult(FilterStatus.dropped, "crypto / blockchain / web3 role")

    return FilterResult(FilterStatus.passed)


def evaluate_profile(raw: RawJob, config: Optional[FilterConfig] = None) -> FilterResult:
    """One profile's personal rules (its countries and visa sponsorship,
    home-state allow-list, seniority, title keywords, excluded and required
    tech) on a posting that already passed ``evaluate_shared``.
    Applied per profile by ``matching``, against the stored posting."""
    cfg = config or _BUILTIN_DEFAULT
    title = raw.title or ""
    haystack = _haystack(raw)
    tags_lower = {t.lower() for t in raw.tags}

    # 4b. This profile's own countries: the scrape kept the posting for the
    #     union of every profile's, so another profile's country is dropped here.
    where = _where(raw, cfg.markets)
    if where.status is FilterStatus.dropped:
        return FilterResult(FilterStatus.dropped, where.reason)

    # 4c. Sponsorship: when every country the posting is workable from for this
    #     profile is one it needs a visa for, a posting that won't sponsor is out.
    if (
        where.markets
        and all(m.needs_sponsorship for m in where.markets)
        and NO_SPONSORSHIP.search(raw.description or "")
    ):
        return FilterResult(FilterStatus.dropped, "won't sponsor a work visa")

    # 5. State allow-list must include the user's home state (when one is configured;
    #    an unset home state skips this rule rather than assuming any state).
    if cfg.home_state and _has_state_allowlist_excluding_home(haystack, cfg):
        return FilterResult(
            FilterStatus.dropped, f"state allow-list excludes {cfg.home_state}"
        )

    # 6. Seniority (configurable)
    if cfg.seniority_re is not None and not cfg.seniority_re.search(title):
        return FilterResult(
            FilterStatus.dropped,
            "title not Senior/Staff/Principal/Lead (or configured equivalent)",
        )

    # 6b. Job function (configurable): the title must name one of the profile's
    #     title keywords, so a PM profile doesn't match "Senior Software Engineer"
    #     just because the description mentions Jira and AWS.
    if cfg.title_re is not None and not cfg.title_re.search(title):
        return FilterResult(
            FilterStatus.dropped, "title doesn't match any of your title keywords"
        )

    # 7. Excluded-tech check (before required-tech — an excluded term may itself
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

    # 8. Required-tech reference
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

    # A region-wide location ("Remote - EMEA") that covers one of the profile's
    # countries but names none: everything else passed, so the user decides.
    if where.status is FilterStatus.manual:
        return FilterResult(FilterStatus.manual, where.reason)

    return FilterResult(FilterStatus.passed)
