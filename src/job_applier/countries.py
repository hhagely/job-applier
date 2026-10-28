"""Where a posting can be done, and the countries a search can include.

Two halves. ``posting_countries`` pulls the structured country fields most ATSs
send out of the source's raw payload, so the filter can place a bare office name
("Bengaluru-HQ", "Noida", "Hub - Kitchener") at scrape time and (via
``JobPosting.countries``) on every re-match. A source that sends none
(Greenhouse, the aggregators) yields ``[]``, and the filter falls back to the
location text, which ``text_countries`` / ``text_regions`` read.

``COUNTRIES`` is the catalog behind both, and behind the countries a profile can
add to its search (``Market``). A country needs an entry here to be searchable:
the filter can only honor a country it knows how to recognize in a posting.

Stdlib-only, because ``contracts`` imports it.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any, Optional

US = "US"


@dataclass(frozen=True)
class Country:
    """One searchable country.

    ``code`` (ISO 3166 alpha-2) is what gets stored. ``codes`` and ``names`` are
    the other spellings a source payload or the user may give; ``names`` and
    ``places`` (regions, major cities) also place a free-text location here,
    matched case-insensitively as whole words. ``text_codes`` are short codes
    safe to read in location text only when written in capitals ("UK"). Place
    names shared with a US town (Cambridge, Birmingham, Athens, Perth, York) are
    left out, so a bare US location is never read as foreign.
    """

    code: str
    name: str
    codes: tuple[str, ...] = ()
    names: tuple[str, ...] = ()
    places: tuple[str, ...] = ()
    text_codes: tuple[str, ...] = ()


COUNTRIES: tuple[Country, ...] = (
    Country("US", "United States", ("USA",), ("United States of America", "U.S.", "U.S.A.")),
    Country(
        "CA",
        "Canada",
        ("CAN",),
        (),
        ("Toronto", "Vancouver", "Montreal", "Ottawa", "Calgary", "Ontario",
         "British Columbia", "Quebec", "Alberta"),
    ),
    Country("MX", "Mexico", ("MEX",), ("México",), ("Guadalajara",)),
    Country("BR", "Brazil", ("BRA",), ("Brasil",), ("Sao Paulo", "São Paulo")),
    Country("AR", "Argentina", ("ARG",), (), ("Buenos Aires",)),
    Country("CL", "Chile", ("CHL",)),
    Country("CO", "Colombia", ("COL",), (), ("Bogota", "Bogotá", "Medellin", "Medellín")),
    Country("PE", "Peru", ("PER",)),
    Country(
        "GB",
        "United Kingdom",
        ("GBR", "UK"),
        ("Great Britain", "Britain", "England", "Scotland", "Wales", "Northern Ireland",
         "United Kingdom of Great Britain and Northern Ireland", "U.K."),
        ("London", "Manchester", "Edinburgh", "Glasgow", "Aberdeen", "Dundee", "Inverness",
         "Stirling", "Belfast", "Cardiff", "Swansea", "Leeds", "Liverpool", "Sheffield",
         "Nottingham", "Leicester", "Coventry", "Brighton", "Southampton", "Milton Keynes",
         "Newcastle upon Tyne"),
        ("UK",),
    ),
    Country("IE", "Ireland", ("IRL",), (), ("Dublin", "Cork")),
    Country(
        "DE", "Germany", ("DEU",), ("Deutschland",),
        ("Berlin", "Munich", "München", "Hamburg", "Frankfurt"),
    ),
    Country("FR", "France", ("FRA",), (), ("Paris",)),
    Country("ES", "Spain", ("ESP",), ("España",), ("Madrid", "Barcelona")),
    Country("PT", "Portugal", ("PRT",), (), ("Lisbon", "Porto")),
    Country("IT", "Italy", ("ITA",), ("Italia",), ("Rome", "Milan")),
    Country(
        "NL", "Netherlands", ("NLD",), ("The Netherlands", "Holland"), ("Amsterdam", "Rotterdam")
    ),
    Country("BE", "Belgium", ("BEL",), (), ("Brussels",)),
    Country("LU", "Luxembourg", ("LUX",)),
    Country(
        "PL", "Poland", ("POL",), (), ("Warsaw", "Krakow", "Kraków", "Wroclaw", "Wrocław")
    ),
    Country("RO", "Romania", ("ROU",), (), ("Bucharest",)),
    Country("UA", "Ukraine", ("UKR",), (), ("Kyiv", "Kiev")),
    Country("AT", "Austria", ("AUT",), (), ("Vienna",)),
    Country("CH", "Switzerland", ("CHE",), (), ("Zurich", "Zürich", "Geneva")),
    Country("SE", "Sweden", ("SWE",), (), ("Stockholm",)),
    Country("NO", "Norway", ("NOR",), (), ("Oslo",)),
    Country("DK", "Denmark", ("DNK",), (), ("Copenhagen",)),
    Country("FI", "Finland", ("FIN",), (), ("Helsinki",)),
    Country("IS", "Iceland", ("ISL",)),
    Country("CZ", "Czech Republic", ("CZE",), ("Czechia", "Czech"), ("Prague",)),
    Country("HU", "Hungary", ("HUN",), (), ("Budapest",)),
    Country("SK", "Slovakia", ("SVK",)),
    Country("BG", "Bulgaria", ("BGR",), (), ("Sofia",)),
    Country("GR", "Greece", ("GRC",)),
    Country("EE", "Estonia", ("EST",), (), ("Tallinn",)),
    Country("LV", "Latvia", ("LVA",), (), ("Riga",)),
    Country("LT", "Lithuania", ("LTU",), (), ("Vilnius",)),
    Country("JP", "Japan", ("JPN",), (), ("Tokyo",)),
    Country("CN", "China", ("CHN",), (), ("Beijing", "Shanghai", "Shenzhen")),
    Country("KR", "South Korea", ("KOR",), ("Korea", "Republic of Korea"), ("Seoul",)),
    Country("SG", "Singapore", ("SGP",)),
    Country(
        "IN",
        "India",
        ("IND",),
        (),
        ("Bengaluru", "Bangalore", "Hyderabad", "Pune", "Noida", "Gurugram", "Gurgaon",
         "Chennai", "Mumbai", "New Delhi", "Kolkata", "Ahmedabad"),
    ),
    Country("ID", "Indonesia", ("IDN",), (), ("Jakarta",)),
    Country("PH", "Philippines", ("PHL",), (), ("Manila",)),
    Country("VN", "Vietnam", ("VNM",), ("Viet Nam",), ("Hanoi", "Ho Chi Minh City")),
    Country("TH", "Thailand", ("THA",), (), ("Bangkok",)),
    Country("MY", "Malaysia", ("MYS",), (), ("Kuala Lumpur",)),
    Country("AU", "Australia", ("AUS",), (), ("Sydney", "Melbourne", "Brisbane")),
    Country("NZ", "New Zealand", ("NZL",), (), ("Auckland",)),
    Country("ZA", "South Africa", ("ZAF",), (), ("Cape Town", "Johannesburg")),
    Country("NG", "Nigeria", ("NGA",), (), ("Lagos",)),
    Country("KE", "Kenya", ("KEN",), (), ("Nairobi",)),
    Country("EG", "Egypt", ("EGY",), (), ("Cairo",)),
    Country("MA", "Morocco", ("MAR",)),
    Country("IL", "Israel", ("ISR",), (), ("Tel Aviv",)),
    Country("TR", "Turkey", ("TUR",), ("Türkiye", "Turkiye"), ("Istanbul",)),
    Country("AE", "United Arab Emirates", ("ARE",), ("UAE",), ("Dubai", "Abu Dhabi")),
    Country("SA", "Saudi Arabia", ("SAU",), ("Saudi",), ("Riyadh",)),
    Country("QA", "Qatar", ("QAT",), (), ("Doha",)),
)

COUNTRY_BY_CODE: dict[str, Country] = {c.code: c for c in COUNTRIES}

# Every spelling a payload or the user may give, lowercased, to its code.
_CODE_BY_KEY: dict[str, str] = {}
for _c in COUNTRIES:
    for _key in (_c.code, _c.name, *_c.codes, *_c.names):
        _CODE_BY_KEY[_key.lower()] = _c.code


def lookup_country(value: Any) -> Optional[Country]:
    """The catalog country a code or name names (any case), else ``None``."""
    if not isinstance(value, str):
        return None
    code = _CODE_BY_KEY.get(value.strip().lower())
    return COUNTRY_BY_CODE.get(code) if code else None


def normalize_country(value: Any) -> Optional[str]:
    """The alpha-2 code for any spelling of a catalog country ("US" for the
    United States), else the value as given. Idempotent, so a stored value can
    be normalized again on read."""
    if not isinstance(value, str) or not value.strip():
        return None
    value = value.strip()
    country = lookup_country(value)
    return country.code if country else value


def _words_pattern(token: str) -> str:
    # Any whitespace run between the words of a multi-word name.
    return r"\s+".join(re.escape(part) for part in token.split())


def _token_key(text: str) -> str:
    return " ".join(text.lower().split())


# Free-text place names of every non-US country (the US is read by the filter's
# own hints: state codes, "Americas", ...). One alternation, longest first so
# "New Delhi" wins over a shorter overlap; a match maps back to its country. The
# trailing lookahead (not \b) lets a dotted name like "U.K." end on its dot.
_PLACE_CODE: dict[str, str] = {}
for _c in COUNTRIES:
    if _c.code == US:
        continue
    for _token in (_c.name, *_c.names, *_c.places):
        _PLACE_CODE[_token_key(_token)] = _c.code
_PLACE_RE = re.compile(
    r"\b(?:"
    + "|".join(_words_pattern(t) for t in sorted(_PLACE_CODE, key=len, reverse=True))
    + r")(?!\w)",
    re.IGNORECASE,
)
_TEXT_CODE: dict[str, str] = {t: c.code for c in COUNTRIES for t in c.text_codes}
_TEXT_CODE_RE = re.compile(r"\b(" + "|".join(map(re.escape, _TEXT_CODE)) + r")\b")


def text_countries(text: str) -> set[str]:
    """The non-US catalog countries a free-text location names."""
    if not text:
        return set()
    found = {_PLACE_CODE[_token_key(m.group())] for m in _PLACE_RE.finditer(text)}
    found.update(_TEXT_CODE[m.group()] for m in _TEXT_CODE_RE.finditer(text))
    return found


_EUROPE = frozenset(
    "GB IE DE FR ES PT IT NL BE LU PL RO UA AT CH SE NO DK FI IS CZ HU SK BG GR EE LV LT".split()
)
_APAC = frozenset("JP CN KR SG IN ID PH VN TH MY AU NZ".split())
_LATAM = frozenset("MX BR AR CL CO PE".split())

# Region words a location may name instead of a country, with the catalog
# countries each covers. "Remote - EMEA" says neither yes nor no about any one
# country in it, which is why the filter leaves those to the user.
REGIONS: dict[str, frozenset[str]] = {
    "emea": _EUROPE | frozenset("IL TR AE SA QA ZA NG KE EG MA".split()),
    "europe": _EUROPE,
    "european union": _EUROPE,
    "eu": _EUROPE,
    "apac": _APAC,
    "asia pacific": _APAC,
    "asia-pacific": _APAC,
    "latam": _LATAM,
    "latin america": _LATAM,
}
_REGION_RE = re.compile(
    r"\b(" + "|".join(_words_pattern(r) for r in sorted(REGIONS, key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)


def text_regions(text: str) -> set[str]:
    """The region words (keys of ``REGIONS``) a free-text location names."""
    if not text:
        return set()
    return {_token_key(m.group()) for m in _REGION_RE.finditer(text)}


@dataclass(frozen=True)
class Market:
    """A country a profile searches in. The US is always one (remote only, no
    sponsorship); ``SearchProfile.markets`` lists the rest. ``hybrid_cities`` are
    where a hybrid or on-site role is acceptable instead of remote-only."""

    country: str
    needs_sponsorship: bool = False
    hybrid_cities: tuple[str, ...] = ()


US_MARKET = Market(US)

MAX_HYBRID_CITIES = 10
_MAX_CITY_LEN = 60


def _clean_cities(cities: Any) -> tuple[str, ...]:
    out: list[str] = []
    for city in cities if isinstance(cities, list) else []:
        if not isinstance(city, str):
            continue
        city = " ".join(city.split())[:_MAX_CITY_LEN]
        if city and city.lower() not in {c.lower() for c in out}:
            out.append(city)
    return tuple(out[:MAX_HYBRID_CITIES])


def normalize_markets(rows: Any) -> list[dict]:
    """Validate the countries a user adds to a profile, for storage.

    Each row is ``{"country", "needs_sponsorship", "hybrid_cities"}``; the
    country may be any spelling ``lookup_country`` knows. Raises ``ValueError``
    on a country outside the catalog (the API turns that into a 422) rather than
    storing one the filter can't recognize. The US is dropped (it's always
    searched), a repeated country keeps its first row, and city lists are
    trimmed and deduped.
    """
    out: list[dict] = []
    seen: set[str] = set()
    for row in rows or []:
        raw_country = row.get("country") if isinstance(row, dict) else None
        country = lookup_country(raw_country)
        if country is None:
            raise ValueError(f"unsupported country: {raw_country!r}")
        if country.code == US or country.code in seen:
            continue
        seen.add(country.code)
        out.append(
            {
                "country": country.code,
                "needs_sponsorship": bool(row.get("needs_sponsorship")),
                "hybrid_cities": list(_clean_cities(row.get("hybrid_cities"))),
            }
        )
    return out


def markets_from_rows(rows: Any) -> tuple[Market, ...]:
    """A profile's stored markets as ``Market``s, the US first. Lenient where
    ``normalize_markets`` is strict: a row naming a country that has since left
    the catalog is skipped, never fatal to the filter."""
    markets = [US_MARKET]
    seen = {US}
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        country = lookup_country(row.get("country"))
        if country is None or country.code in seen:
            continue
        seen.add(country.code)
        markets.append(
            Market(
                country.code,
                needs_sponsorship=bool(row.get("needs_sponsorship")),
                hybrid_cities=_clean_cities(row.get("hybrid_cities")),
            )
        )
    return tuple(markets)


def _get(d: Any, *path: str) -> Any:
    for key in path:
        if not isinstance(d, dict):
            return None
        d = d.get(key)
    return d


def _dicts(items: Any) -> list[dict]:
    return [x for x in items if isinstance(x, dict)] if isinstance(items, list) else []


# One extractor per source, reading the payload exactly as that adapter stores
# it in ``RawJob.raw`` (Workday and Oracle wrap theirs as {"list", "detail"}).


def _ashby(raw: dict) -> Iterable[Any]:
    yield _get(raw, "address", "postalAddress", "addressCountry")
    for loc in _dicts(raw.get("secondaryLocations")):
        yield _get(loc, "address", "postalAddress", "addressCountry")


def _lever(raw: dict) -> Iterable[Any]:
    yield raw.get("country")


def _workday(raw: dict) -> Iterable[Any]:
    yield _get(raw, "detail", "country", "descriptor")
    yield _get(raw, "detail", "jobRequisitionLocation", "country", "descriptor")


def _smartrecruiters(raw: dict) -> Iterable[Any]:
    yield _get(raw, "location", "country")


def _jibe(raw: dict) -> Iterable[Any]:
    yield raw.get("country_code") or raw.get("country")
    for loc in _dicts(raw.get("additional_locations")):
        yield loc.get("country_code") or loc.get("country")


def _workable(raw: dict) -> Iterable[Any]:
    yield _get(raw, "location", "countryCode")
    for loc in _dicts(raw.get("locations")):
        yield loc.get("countryCode")


def _oracle(raw: dict) -> Iterable[Any]:
    for part in ("list", "detail"):
        yield _get(raw, part, "PrimaryLocationCountry")
        for loc in _dicts(_get(raw, part, "secondaryLocations")):
            yield loc.get("CountryCode")


_EXTRACTORS: dict[str, Callable[[dict], Iterable[Any]]] = {
    "ashby": _ashby,
    "lever": _lever,
    "workday": _workday,
    "smartrecruiters": _smartrecruiters,
    "jibe": _jibe,
    "workable": _workable,
    "oracle": _oracle,
}


def posting_countries(source: str, raw: Any) -> list[str]:
    """Every country ``source``'s payload lists for the posting, US spelled
    ``"US"``, deduped in order. ``[]`` when the source sends none."""
    extract = _EXTRACTORS.get(source)
    if extract is None or not isinstance(raw, dict):
        return []
    out: list[str] = []
    for value in extract(raw):
        country = normalize_country(value)
        if country and country not in out:
            out.append(country)
    return out
