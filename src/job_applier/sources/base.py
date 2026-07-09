from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Protocol


def parse_iso_date(value: Optional[str]) -> Optional[datetime]:
    """Parse an ISO-8601 timestamp, tolerating a trailing ``Z``. Returns ``None``
    for empty, non-string, or unparseable input. Shared by the source adapters."""
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def parse_date_multi(value: Optional[str]) -> Optional[datetime]:
    """ISO-8601 first, then a couple of date-only / naive formats stamped UTC.

    For sources (Workday, Oracle) whose feeds sometimes emit non-ISO date
    strings. Returns ``None`` when nothing parses.
    """
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


@dataclass
class RawJob:
    source: str
    source_id: str
    url: str
    title: str
    company_name: str
    description: str
    location: Optional[str] = None
    remote: bool = True
    employment_type: Optional[str] = None
    posted_at: Optional[datetime] = None
    tags: list[str] = field(default_factory=list)
    raw: dict = field(default_factory=dict)


class SourceAdapter(Protocol):
    name: str

    def fetch(self) -> Iterable[RawJob]: ...
