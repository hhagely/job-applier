"""Source adapter registry.

The DB (``SourceSlug`` table) is the runtime source of truth for which
per-company boards (Greenhouse / Lever / Ashby / Workday) to ingest from.
``companies.py`` exists only as a one-time seed for fresh installs.

The aggregator sources (RemoteOK, WeWorkRemotely, HackerNews) take no slug
config — they always fetch a fixed set of feeds.
"""

from sqlmodel import Session, select

from job_applier.models import SourceSlug, engine
from job_applier.sources.ashby import AshbySource
from job_applier.sources.base import RawJob, SourceAdapter
from job_applier.sources.greenhouse import GreenhouseSource
from job_applier.sources.hackernews import HackerNewsHiringSource
from job_applier.sources.lever import LeverSource
from job_applier.sources.remoteok import RemoteOKSource
from job_applier.sources.smartrecruiters import SmartRecruitersSource
from job_applier.sources.workable import WorkableSource
from job_applier.sources.weworkremotely import WeWorkRemotelySource
from job_applier.sources.workday import WorkdaySource
from job_applier.sources.ycombinator import YCombinatorSource


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
        AshbySource(_enabled_slugs("ashby")),
        WorkdaySource(_enabled_slugs("workday")),
        WorkableSource(_enabled_slugs("workable")),
        SmartRecruitersSource(_enabled_slugs("smartrecruiters")),
        RemoteOKSource(),
        WeWorkRemotelySource(),
        HackerNewsHiringSource(),
        YCombinatorSource(),
    ]


__all__ = [
    "AshbySource",
    "GreenhouseSource",
    "HackerNewsHiringSource",
    "LeverSource",
    "RawJob",
    "RemoteOKSource",
    "SmartRecruitersSource",
    "SourceAdapter",
    "WeWorkRemotelySource",
    "WorkableSource",
    "WorkdaySource",
    "YCombinatorSource",
    "get_all_sources",
]
