from job_applier.sources.adzuna import AdzunaSource
from job_applier.sources.base import RawJob, SourceAdapter
from job_applier.sources.companies import GREENHOUSE_COMPANIES, LEVER_COMPANIES
from job_applier.sources.greenhouse import GreenhouseSource
from job_applier.sources.lever import LeverSource
from job_applier.sources.remotive import RemotiveSource

ALL_SOURCES: list[SourceAdapter] = [
    RemotiveSource(),
    GreenhouseSource(GREENHOUSE_COMPANIES),
    LeverSource(LEVER_COMPANIES),
    AdzunaSource(),  # silently no-ops if env vars not set
]

__all__ = [
    "ALL_SOURCES",
    "AdzunaSource",
    "GreenhouseSource",
    "LeverSource",
    "RawJob",
    "RemotiveSource",
    "SourceAdapter",
]
