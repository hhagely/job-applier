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
    """Build the ingest source list from current DB state.

    The active ``FilterConfig`` is passed into Workable + SmartRecruiters so
    those adapters can skip the per-job detail fetch for titles that already
    fail seniority or sales rules — the dominant cost on those sources, and
    the only realistic way to stay under Workable's IP rate limit on a
    multi-hundred-slug board sweep.
    """
    # Lazy import — ``filters.rules`` imports ``RawJob`` from ``sources.base``,
    # so a module-level import here would form a cycle.
    from job_applier.filters import load_active_config

    filter_config = load_active_config()
    return [
        GreenhouseSource(_enabled_slugs("greenhouse")),
        LeverSource(_enabled_slugs("lever")),
        AshbySource(_enabled_slugs("ashby")),
        WorkdaySource(_enabled_slugs("workday")),
        WorkableSource(_enabled_slugs("workable"), filter_config=filter_config),
        SmartRecruitersSource(
            _enabled_slugs("smartrecruiters"), filter_config=filter_config
        ),
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
