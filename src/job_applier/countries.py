"""Where a posting can be done, from the structured country fields its source sends.

The US-only rule used to read only the free-text location, which can't place a
bare office name ("Bengaluru-HQ", "Noida", "Hub - Kitchener"). Most ATSs send a
country alongside it; ``posting_countries`` pulls those out of the source's raw
payload so the filter can use them, at scrape time and (via
``JobPosting.countries``) on every re-match. A source that sends none
(Greenhouse, the aggregators) yields ``[]``, and the filter falls back to the
location text.

Stdlib-only, because ``contracts`` imports it.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any, Optional

US = "US"

_US_NAMES = frozenset(
    {"us", "usa", "u.s.", "u.s.a.", "united states", "united states of america"}
)


def normalize_country(value: Any) -> Optional[str]:
    """``"US"`` for any spelling of the United States, else the value as given."""
    if not isinstance(value, str) or not value.strip():
        return None
    value = value.strip()
    return US if value.lower() in _US_NAMES else value


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
