"""Source-adapter contract.

The shared ``RawJob`` vocabulary and the date-parsing helpers now live in
``job_applier.contracts`` (dependency-free, so ``filters`` can import them without
a cycle). They are re-exported here so the adapters can keep importing everything
they need from one place — ``from job_applier.sources.base import RawJob, ...``.
"""

from collections.abc import Iterable
from typing import Protocol

from job_applier.contracts import RawJob, parse_date_multi, parse_iso_date

__all__ = ["RawJob", "SourceAdapter", "parse_date_multi", "parse_iso_date"]


class SourceAdapter(Protocol):
    name: str

    def fetch(self) -> Iterable[RawJob]: ...
