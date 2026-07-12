from __future__ import annotations

import pytest

from job_applier.filters.rules import (
    build_config,
    evaluate,
    normalize_home_state,
    title_quick_fail,
)
from job_applier.models.db import FilterStatus

# A filter config that only sets a home state (other lists empty, so the seniority
# and tech rules are skipped) — lets the state-allow-list tests below isolate rule 3.
MO_CONFIG = build_config(
    role_titles=[], seniority_terms=[], required_tech=[], excluded_tech=[], home_state="Missouri"
)
CA_CONFIG = build_config(
    role_titles=[], seniority_terms=[], required_tech=[], excluded_tech=[], home_state="California"
)


def test_passes_senior_remote_typescript_us(make_raw):
    result = evaluate(make_raw())
    assert result.status is FilterStatus.passed


def test_drops_when_not_remote(make_raw):
    result = evaluate(make_raw(remote=False))
    assert result.status is FilterStatus.dropped
    assert "remote" in (result.reason or "")


def test_drops_hybrid_in_title(make_raw):
    result = evaluate(make_raw(title="Senior Engineer (Hybrid)"))
    assert result.status is FilterStatus.dropped


def test_drops_non_us_only_location(make_raw):
    result = evaluate(make_raw(location="Berlin, Germany"))
    assert result.status is FilterStatus.dropped
    assert "non-US" in (result.reason or "")


def test_keeps_non_us_city_with_us_hint(make_raw):
    # "Remote — US/Canada" should not be dropped just because Canada is named.
    result = evaluate(make_raw(location="Remote — US/Canada"))
    assert result.status is FilterStatus.passed


@pytest.mark.parametrize(
    "location",
    ["Austin, TX", "San Francisco, CA", "New York, NY", "Remote - Austin, TX"],
)
def test_keeps_us_city_state_without_country_token(location: str, make_raw):
    # A bare "City, ST" US location (no explicit country) must not be dropped as
    # non-US — the 2-letter state code is a US hint.
    result = evaluate(make_raw(location=location))
    assert result.status is FilterStatus.passed


@pytest.mark.parametrize("location", ["Toronto, ON", "Vancouver, BC", "London, UK"])
def test_drops_non_us_city_with_non_us_region_code(location: str, make_raw):
    # Canadian province / UK codes are not US-state codes, so these stay dropped.
    result = evaluate(make_raw(location=location))
    assert result.status is FilterStatus.dropped
    assert "non-US" in (result.reason or "")


def test_drops_non_senior_title(make_raw):
    result = evaluate(make_raw(title="Software Engineer"))
    assert result.status is FilterStatus.dropped
    assert "Senior" in (result.reason or "")


@pytest.mark.parametrize(
    "title",
    [
        "Senior Solutions Engineer",
        "Staff Sales Engineer",
        "Senior Account Executive",
        "Head of Partnerships",
    ],
)
def test_drops_sales_titles(title: str, make_raw):
    result = evaluate(make_raw(title=title))
    assert result.status is FilterStatus.dropped
    reason = (result.reason or "").lower()
    assert "sales" in reason or "biz" in reason


@pytest.mark.parametrize(
    "title,description",
    [
        ("Senior Blockchain Engineer", "We use TypeScript and React."),
        ("Senior Software Engineer", "Build our cryptocurrency exchange in TypeScript."),
        ("Senior Software Engineer", "Web3 startup. TypeScript, React, Solidity."),
        ("Senior Software Engineer", "Write smart contracts. TypeScript front end."),
        ("Senior Software Engineer", "DeFi protocol on Ethereum. Node.js backend."),
        ("Senior Software Engineer", "NFT marketplace built with React and TypeScript."),
    ],
)
def test_drops_crypto_blockchain_roles(title: str, description: str, make_raw):
    result = evaluate(make_raw(title=title, description=description))
    assert result.status is FilterStatus.dropped
    assert "crypto" in (result.reason or "").lower()


@pytest.mark.parametrize(
    "description",
    [
        # "cryptography" / "crypto" for security must not trigger the crypto filter.
        "Senior backend role. Strong cryptography and TLS background. TypeScript.",
        # A Java-style Data Access Object pattern shouldn't read as a crypto DAO.
        "We use a DAO layer over Postgres. TypeScript and React on the front end.",
    ],
)
def test_keeps_non_crypto_uses_of_crypto_adjacent_words(description: str, make_raw):
    result = evaluate(make_raw(description=description))
    assert result.status is FilterStatus.passed


def test_drops_angular_in_title(make_raw):
    result = evaluate(make_raw(title="Senior Angular Engineer"))
    assert result.status is FilterStatus.dropped


def test_drops_when_angular_is_only_framework_in_tags(make_raw):
    result = evaluate(
        make_raw(
            description="Frontend engineering role.",
            tags=["angular", "typescript"],
        )
    )
    assert result.status is FilterStatus.dropped


def test_keeps_angular_when_other_framework_in_tags(make_raw):
    result = evaluate(
        make_raw(
            description="Frontend role using TypeScript.",
            tags=["angular", "react", "typescript"],
        )
    )
    assert result.status is FilterStatus.passed


def test_drops_when_no_js_ts_reference(make_raw):
    result = evaluate(
        make_raw(description="We are a Python and Go shop building backend systems.")
    )
    assert result.status is FilterStatus.dropped
    assert "JavaScript" in (result.reason or "")


def test_marks_manual_when_only_short_js_ts_hint(make_raw):
    result = evaluate(
        make_raw(description="Backend role; some js work occasionally.")
    )
    assert result.status is FilterStatus.manual


def test_marks_manual_when_excluded_tech_only_in_description(make_raw):
    # Excluded tech (Angular) shows up in the description while the positive
    # TS/JS signal lives in the tags — so rule 8 passes on the haystack, but the
    # description alone reads excluded-primary. Surface for manual review rather
    # than dropping outright (the rule-7 tail).
    result = evaluate(
        make_raw(
            title="Senior Software Engineer",
            description="Maintain a legacy Angular dashboard plus some backend work.",
            tags=["typescript"],
        )
    )
    assert result.status is FilterStatus.manual
    assert "verify primary stack" in (result.reason or "")


# ---- State allow-list (configurable home-state eligibility) ----
#
# The home state lives on the SearchProfile and is passed via the FilterConfig, so
# these tests hand `evaluate` an explicit config. With NO home state configured the
# rule is skipped entirely (see test_state_rule_skipped_when_no_home_state).


def test_keeps_when_no_state_list_present(make_raw):
    # No state restriction language at all — assume open anywhere.
    result = evaluate(
        make_raw(description="We use TypeScript and React. Remote-first team."), MO_CONFIG
    )
    assert result.status is FilterStatus.passed


def test_drops_when_state_list_excludes_home_state(make_raw):
    result = evaluate(
        make_raw(
            description=(
                "We use TypeScript and React. We are currently hiring employees in "
                "California, New York, Texas, and Washington."
            )
        ),
        MO_CONFIG,
    )
    assert result.status is FilterStatus.dropped
    assert "Missouri" in (result.reason or "")


def test_keeps_when_state_list_includes_home_state(make_raw):
    result = evaluate(
        make_raw(
            description=(
                "TypeScript / React role. We hire employees in California, Missouri, and Texas."
            )
        ),
        MO_CONFIG,
    )
    assert result.status is FilterStatus.passed


def test_keeps_when_state_list_includes_home_state_abbrev(make_raw):
    # The home state's two-letter code counts as present, so "MO" satisfies the list.
    result = evaluate(
        make_raw(description="React role. We can only hire in CA, NY, MO, and TX."),
        MO_CONFIG,
    )
    assert result.status is FilterStatus.passed


def test_drops_single_state_must_reside(make_raw):
    result = evaluate(
        make_raw(description="TypeScript / React role. Candidates must reside in California."),
        MO_CONFIG,
    )
    assert result.status is FilterStatus.dropped


def test_keeps_when_nationwide_override_present(make_raw):
    # "We hire in" appears, but "any US state" overrides the implied list.
    result = evaluate(
        make_raw(
            description=(
                "TypeScript / React role. We hire in any US state. Offices in California "
                "and New York for those who want them."
            )
        ),
        MO_CONFIG,
    )
    assert result.status is FilterStatus.passed


def test_keeps_when_states_named_without_restriction_phrase(make_raw):
    # State names mentioned (offices) but no allow-list trigger — should pass.
    result = evaluate(
        make_raw(
            description=(
                "TypeScript / React role. Our offices are in California and New York, "
                "but the team is fully remote across the US."
            )
        ),
        MO_CONFIG,
    )
    assert result.status is FilterStatus.passed


def test_state_rule_skipped_when_no_home_state(make_raw):
    # With no home state configured (the built-in default), a state allow-list that
    # would exclude Missouri no longer drops the posting — the rule is off.
    result = evaluate(
        make_raw(
            description=(
                "We use TypeScript and React. We are currently hiring employees in "
                "California, New York, Texas, and Washington."
            )
        )
    )
    assert result.status is FilterStatus.passed


def test_state_rule_is_dynamic_per_home_state(make_raw):
    # The SAME posting that excludes Missouri also excludes California, but a list
    # that includes California passes for a California resident.
    excludes_ca = make_raw(
        description="React role. We are hiring employees in Missouri, New York, and Texas."
    )
    assert evaluate(excludes_ca, CA_CONFIG).status is FilterStatus.dropped
    assert "California" in (evaluate(excludes_ca, CA_CONFIG).reason or "")

    includes_ca = make_raw(
        description="React role. We are hiring employees in California, New York, and Texas."
    )
    assert evaluate(includes_ca, CA_CONFIG).status is FilterStatus.passed


def test_home_state_normalization():
    # Full names and two-letter codes both normalize to the canonical proper name;
    # blank/None means "no home state"; garbage is rejected.
    assert normalize_home_state("Missouri") == "Missouri"
    assert normalize_home_state("missouri") == "Missouri"
    assert normalize_home_state("MO") == "Missouri"
    assert normalize_home_state("mo") == "Missouri"
    assert normalize_home_state("  New York  ") == "New York"
    assert normalize_home_state("") is None
    assert normalize_home_state(None) is None
    with pytest.raises(ValueError):
        normalize_home_state("Ontario")


class TestTitleQuickFail:
    """Cheap title-only pre-check used by adapters that fan out detail fetches.
    The whole point is to drop titles before paying an HTTP round-trip, so
    these tests pin the contract: anything that could pass full evaluation
    must NOT quick-fail, and anything that quick-fails must also fail full
    evaluation by the same rule."""

    def test_drops_non_senior_title(self):
        assert title_quick_fail("Software Engineer") is True

    def test_drops_junior_title(self):
        assert title_quick_fail("Junior Developer") is True

    def test_keeps_senior_title(self):
        assert title_quick_fail("Senior Software Engineer") is False

    def test_keeps_staff_title(self):
        assert title_quick_fail("Staff Engineer, Platform") is False

    def test_drops_sales_engineer(self):
        # "Senior Sales Engineer" passes seniority but is a sales role.
        assert title_quick_fail("Senior Sales Engineer") is True

    def test_drops_account_executive(self):
        assert title_quick_fail("Senior Account Executive") is True

    def test_drops_head_of_partnerships(self):
        assert title_quick_fail("Head of Partnerships") is True

    def test_keeps_blank_seniority_when_no_config(self):
        # Edge case — empty string shouldn't crash, but it should drop since
        # there's no seniority marker.
        assert title_quick_fail("") is True
