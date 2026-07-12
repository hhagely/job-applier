"""Dry-run filter diagnostic (`make diagnose-filter`).

A passed `filter_config` keeps these hermetic — no DB, no network. We use the
built-in default config directly so `diagnose_filter` never opens a session.
"""

from __future__ import annotations

from collections.abc import Iterable

from job_applier.diagnostics import diagnose_filter, format_diagnostic
from job_applier.filters.rules import _BUILTIN_DEFAULT
from job_applier.sources.base import RawJob


def _passing(source: str = "good") -> RawJob:
    return RawJob(
        source=source,
        source_id="pass-1",
        url="https://example.com/1",
        title="Senior Software Engineer",
        company_name="Acme",
        description="We use TypeScript and React on Node.js.",
        location="Remote — US",
        remote=True,
    )


def _dropped(source: str = "good") -> RawJob:
    # Not remote -> dropped by the hard filter.
    return RawJob(
        source=source,
        source_id="drop-1",
        url="https://example.com/2",
        title="Senior Software Engineer",
        company_name="Acme",
        description="We use TypeScript and React on Node.js.",
        location="Austin, TX, United States",
        remote=False,
    )


class _StubSource:
    def __init__(self, name: str, raws: list[RawJob]) -> None:
        self.name = name
        self._raws = raws

    def fetch(self) -> Iterable[RawJob]:
        return iter(self._raws)


class _BoomSource:
    name = "boom"

    def fetch(self) -> Iterable[RawJob]:
        raise RuntimeError("kaboom")


def test_diagnose_buckets_pass_and_drop():
    src = _StubSource("good", [_passing(), _dropped()])
    diag = diagnose_filter(sources=[src], filter_config=_BUILTIN_DEFAULT)

    assert diag.fetched_by_source["good"] == 2
    passed = sum(v for k, v in diag.by_source["good"].items() if k.startswith("passed:"))
    dropped = sum(v for k, v in diag.by_source["good"].items() if k.startswith("dropped:"))
    assert passed == 1
    assert dropped == 1


def test_diagnose_isolates_a_failing_source():
    # A source that raises on fetch must not abort the report — the good source
    # is still counted (this is the tool you reach for to find the bad source).
    diag = diagnose_filter(
        sources=[_BoomSource(), _StubSource("good", [_passing()])],
        filter_config=_BUILTIN_DEFAULT,
    )
    assert diag.fetched_by_source["good"] == 1
    assert "boom" not in diag.fetched_by_source


def test_format_diagnostic_renders_without_error():
    diag = diagnose_filter(
        sources=[_StubSource("good", [_passing(), _dropped()])],
        filter_config=_BUILTIN_DEFAULT,
    )
    report = format_diagnostic(diag)
    assert "Filter diagnostic" in report
    assert "good" in report
