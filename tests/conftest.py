from __future__ import annotations

from collections.abc import Callable

import pytest

from job_applier.sources.base import RawJob


@pytest.fixture(autouse=True, scope="session")
def _isolate_user_data(tmp_path_factory):
    """Point every on-disk path at a throwaway dir for the whole run.

    Without this, any test that enters ``TestClient(app)`` runs the app lifespan,
    whose ``create_db_and_tables()`` migrates whatever ``settings.db_path`` names —
    by default the developer's real ``data/jobs.db``. Tests that need a specific
    path still monkeypatch their own on top of this.
    """
    from job_applier.config import settings
    from job_applier.models import db

    root = tmp_path_factory.mktemp("user-data")
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(settings, "data_dir", root)
        mp.setattr(settings, "db_path", root / "jobs.db")
        mp.setattr(settings, "resumes_dir", root / "resumes")
        mp.setattr(settings, "applications_dir", root / "applications")
        mp.setattr(db, "_engine", None)
        yield
        # Drop the engine bound to the temp DB so nothing outlives the run.
        mp.setattr(db, "_engine", None)


@pytest.fixture(autouse=True)
def rematches(monkeypatch) -> list[int]:
    """Record the profiles whose criteria/blacklist edits asked for a re-match,
    instead of starting the real background task.

    The real task opens its own ``engine()`` session, not the test's overridden
    one, so it could outlive the test and write links into a later test's DB.
    ``match_profile`` is tested directly in test_matching.py.
    """
    from job_applier import matching

    started: list[int] = []
    monkeypatch.setattr(matching, "start_rematch", started.append)
    return started


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
