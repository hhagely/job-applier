"""Source adapter registry.

The DB (``SourceSlug`` table) is the runtime source of truth for which
Greenhouse / Lever boards to ingest from. ``companies.py`` exists only as
a one-time seed for fresh installs. Use ``job-applier refresh-slugs`` to
expand the list from the SimplifyJobs community feed.
"""

from sqlmodel import Session, select

from job_applier.models import SourceSlug, engine
from job_applier.sources.adzuna import AdzunaSource
from job_applier.sources.base import RawJob, SourceAdapter
from job_applier.sources.greenhouse import GreenhouseSource
from job_applier.sources.lever import LeverSource


def _enabled_slugs(source: str) -> list[str]:
    with Session(engine()) as session:
        rows = session.exec(
            select(SourceSlug).where(
                SourceSlug.source == source,
                SourceSlug.enabled.is_(True),  # type: ignore[union-attr]
            )
        ).all()
    return sorted(r.slug for r in rows)


def get_all_sources() -> list[SourceAdapter]:
    """Build the ingest source list from current DB state."""
    return [
        GreenhouseSource(_enabled_slugs("greenhouse")),
        LeverSource(_enabled_slugs("lever")),
        AdzunaSource(),  # silently no-ops if env vars not set
    ]


__all__ = [
    "AdzunaSource",
    "GreenhouseSource",
    "LeverSource",
    "RawJob",
    "SourceAdapter",
    "get_all_sources",
]
