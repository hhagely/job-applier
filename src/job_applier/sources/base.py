from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Protocol


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
