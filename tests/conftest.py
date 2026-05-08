from __future__ import annotations

from collections.abc import Callable

import pytest

from job_applier.sources.base import RawJob


@pytest.fixture
def make_raw() -> Callable[..., RawJob]:
    """Factory for `RawJob` instances with sensible defaults that pass the hard filter.

    Override only the fields the test cares about — everything else stays valid.
    """

    def _make(
        *,
        title: str = "Senior Software Engineer",
        description: str = "We use TypeScript and React on Node.js.",
        location: str | None = "Remote — US",
        remote: bool = True,
        tags: list[str] | None = None,
    ) -> RawJob:
        return RawJob(
            source="test",
            source_id="t-1",
            url="https://example.com/jobs/1",
            title=title,
            company_name="Acme",
            description=description,
            location=location,
            remote=remote,
            tags=tags or [],
        )

    return _make
