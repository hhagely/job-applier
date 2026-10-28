"""Searching countries besides the US (``SearchProfile.markets``)."""

from __future__ import annotations

import pytest

from job_applier.countries import Market, normalize_markets, text_countries
from job_applier.filters.rules import (
    build_config,
    config_for,
    evaluate,
    evaluate_shared,
    union_markets,
)
from job_applier.models.db import FilterStatus, SearchProfile

US_ONLY = build_config(role_titles=[], seniority_terms=[], required_tech=[], excluded_tech=[])


def _cfg(*markets: Market):
    return build_config(
        role_titles=[],
        seniority_terms=[],
        required_tech=[],
        excluded_tech=[],
        markets=(Market("US"), *markets),
    )


UK = _cfg(Market("GB"))
UK_SPONSOR = _cfg(Market("GB", needs_sponsorship=True))
UK_HYBRID = _cfg(Market("GB", hybrid_cities=("Edinburgh", "Livingston")))


@pytest.mark.parametrize(
    ("location", "countries"),
    [
        ("Remote - United Kingdom", None),
        ("Remote - UK", None),
        ("Edinburgh, Scotland", None),
        ("Remote", ["GB"]),
        ("Remote", ["United Kingdom"]),  # a stored pre-normalization spelling
    ],
)
def test_uk_posting_needs_the_uk_market(location, countries, make_raw):
    raw = make_raw(location=location, countries=countries)
    assert evaluate(raw, UK).status is FilterStatus.passed
    dropped = evaluate(raw, US_ONLY)
    assert dropped.status is FilterStatus.dropped
    assert dropped.reason == "location is non-US only"


def test_country_no_profile_searches_is_dropped(make_raw):
    result = evaluate(make_raw(location="Berlin, Germany"), UK)
    assert result.status is FilterStatus.dropped
    assert result.reason == "location is outside the countries you search"


def test_us_postings_still_pass_with_extra_markets(make_raw):
    assert evaluate(make_raw(location="Austin, TX"), UK).status is FilterStatus.passed
    assert evaluate(make_raw(location="Remote"), UK).status is FilterStatus.passed


@pytest.mark.parametrize("location", ["Edinburgh, Scotland", "Livingston, West Lothian"])
def test_hybrid_allowed_in_a_named_city(location, make_raw):
    raw = make_raw(location=location, remote=False)
    assert evaluate(raw, UK_HYBRID).status is FilterStatus.passed
    assert evaluate(raw, UK).status is FilterStatus.dropped


@pytest.mark.parametrize(
    ("location", "countries"),
    [("London, England", None), ("Edinburgh, TX", None), ("Edinburgh", ["US"])],
)
def test_hybrid_elsewhere_stays_dropped(location, countries, make_raw):
    # Another UK city, or a US town that shares the name, isn't the city allowed.
    raw = make_raw(location=location, remote=False, countries=countries)
    result = evaluate(raw, UK_HYBRID)
    assert result.status is FilterStatus.dropped
    assert result.reason == "not remote"


def test_region_wide_location_is_manual_for_a_covered_market(make_raw):
    raw = make_raw(location="Remote - EMEA")
    result = evaluate(raw, UK)
    assert result.status is FilterStatus.manual
    assert "United Kingdom" in (result.reason or "")
    assert evaluate(raw, US_ONLY).status is FilterStatus.dropped


@pytest.mark.parametrize(
    "line",
    [
        "Unfortunately we are unable to offer visa sponsorship.",
        "We do not sponsor visas for this role.",
        "You must have the right to work in the UK.",
        "Visa sponsorship is not available.",
    ],
)
def test_no_sponsorship_drops_where_sponsorship_is_needed(line, make_raw):
    raw = make_raw(location="Remote - UK", description=f"TypeScript and React. {line}")
    result = evaluate(raw, UK_SPONSOR)
    assert result.status is FilterStatus.dropped
    assert result.reason == "won't sponsor a work visa"
    assert evaluate(raw, UK).status is FilterStatus.passed


def test_no_sponsorship_ignored_when_a_market_needs_none(make_raw):
    # Also open to the US, where this profile needs no visa.
    raw = make_raw(
        location="Remote - UK or US",
        description="Must be authorized to work without sponsorship.",
    )
    assert evaluate(raw, UK_SPONSOR).status is FilterStatus.passed


def test_scrape_keeps_what_any_profile_wants(make_raw):
    raw = make_raw(location="Edinburgh, Scotland", remote=False)
    assert evaluate_shared(raw).status is FilterStatus.dropped
    markets = union_markets([US_ONLY, UK, UK_HYBRID])
    assert evaluate_shared(raw, markets).status is FilterStatus.passed
    assert evaluate(raw, US_ONLY).status is FilterStatus.dropped


def test_config_for_reads_profile_markets():
    profile = SearchProfile(
        markets=[{"country": "GB", "needs_sponsorship": True, "hybrid_cities": ["Glasgow"]}]
    )
    cfg = config_for(profile)
    assert [m.country for m in cfg.markets] == ["US", "GB"]
    assert cfg.markets[1] == Market("GB", needs_sponsorship=True, hybrid_cities=("Glasgow",))


def test_normalize_markets():
    rows = normalize_markets(
        [
            {"country": "uk", "needs_sponsorship": True, "hybrid_cities": [" Edinburgh ", "edinburgh", ""]},
            {"country": "United States"},
            {"country": "GB"},
        ]
    )
    assert rows == [{"country": "GB", "needs_sponsorship": True, "hybrid_cities": ["Edinburgh"]}]
    with pytest.raises(ValueError):
        normalize_markets([{"country": "Narnia"}])


@pytest.mark.parametrize("location", ["Austin, TX", "New York", "Cambridge, MA", "Remote - US"])
def test_us_locations_name_no_foreign_country(location):
    assert text_countries(location) == set()
