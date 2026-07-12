"""Source adapter registry.

The DB (``SourceSlug`` table) is the runtime source of truth for which
per-company boards (Greenhouse / Lever / Ashby / Workday / Workable /
SmartRecruiters / Jibe / Oracle) to ingest from. ``companies.py`` exists only
as a one-time seed for fresh installs. The registered set is whatever
``get_all_sources`` builds — keep this list in step with it.

The aggregator sources (RemoteOK, WeWorkRemotely, HackerNews, YCombinator)
take no slug config — they always fetch a fixed set of feeds.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlmodel import Session, select

from job_applier.models import SourceSlug, engine
from job_applier.sources.ashby import AshbySource
from job_applier.sources.base import RawJob, SourceAdapter
from job_applier.sources.greenhouse import GreenhouseSource
from job_applier.sources.hackernews import HackerNewsHiringSource
from job_applier.sources.jibe import JibeSource
from job_applier.sources.lever import LeverSource
from job_applier.sources.oracle import OracleSource
from job_applier.sources.remoteok import RemoteOKSource
from job_applier.sources.smartrecruiters import SmartRecruitersSource
from job_applier.sources.workable import WorkableSource
from job_applier.sources.weworkremotely import WeWorkRemotelySource
from job_applier.sources.workday import WorkdaySource
from job_applier.sources.ycombinator import YCombinatorSource

if TYPE_CHECKING:
    from job_applier.filters import FilterConfig


def _enabled_slugs(source: str) -> list[str]:
    with Session(engine()) as session:
        rows = session.exec(
            select(SourceSlug).where(
                SourceSlug.source == source,
                SourceSlug.enabled.is_(True),  # type: ignore[union-attr]
            )
        ).all()
    return sorted(r.slug for r in rows)


def get_all_sources(filter_config: FilterConfig | None = None) -> list[SourceAdapter]:
    """Build the ingest source list from current DB state.

    ``filter_config`` (the active ``FilterConfig``) is passed into Workable +
    SmartRecruiters so those adapters can skip the per-job detail fetch for titles
    that already fail seniority or sales rules — the dominant cost on those sources,
    and the only realistic way to stay under Workable's IP rate limit on a
    multi-hundred-slug board sweep. Callers that actually fetch (ingest, diagnose)
    load the config once and inject it here, so it isn't loaded twice per run; the
    count-only caller (the ingest endpoint's source total) can omit it.
    """
    return [
        GreenhouseSource(_enabled_slugs("greenhouse")),
        LeverSource(_enabled_slugs("lever")),
        AshbySource(_enabled_slugs("ashby")),
        WorkdaySource(_enabled_slugs("workday")),
        WorkableSource(_enabled_slugs("workable"), filter_config=filter_config),
        SmartRecruitersSource(
            _enabled_slugs("smartrecruiters"), filter_config=filter_config
        ),
        JibeSource(_enabled_slugs("jibe")),
        OracleSource(_enabled_slugs("oracle")),
        RemoteOKSource(),
        WeWorkRemotelySource(),
        HackerNewsHiringSource(),
        YCombinatorSource(),
    ]


__all__ = [
    "AshbySource",
    "GreenhouseSource",
    "HackerNewsHiringSource",
    "JibeSource",
    "LeverSource",
    "OracleSource",
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
