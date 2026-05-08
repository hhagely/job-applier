from job_applier.sources.base import RawJob, SourceAdapter
from job_applier.sources.remotive import RemotiveSource

ALL_SOURCES: list[SourceAdapter] = [RemotiveSource()]

__all__ = ["ALL_SOURCES", "RawJob", "RemotiveSource", "SourceAdapter"]
