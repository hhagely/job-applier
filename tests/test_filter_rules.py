from __future__ import annotations

import pytest

from job_applier.filters.rules import evaluate
from job_applier.models.db import FilterStatus


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
