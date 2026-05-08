from collections.abc import Iterable
from datetime import datetime

import httpx

from job_applier.sources.base import RawJob

REMOTIVE_API = "https://remotive.com/api/remote-jobs"


class RemotiveSource:
    name = "remotive"

    def __init__(self, category: str = "software-dev", limit: int = 200) -> None:
        self.category = category
        self.limit = limit

    def fetch(self) -> Iterable[RawJob]:
        params = {"category": self.category, "limit": self.limit}
        with httpx.Client(timeout=30.0, follow_redirects=True) as client:
            resp = client.get(REMOTIVE_API, params=params)
            resp.raise_for_status()
            data = resp.json()

        for item in data.get("jobs", []):
            yield RawJob(
                source=self.name,
                source_id=str(item["id"]),
                url=item["url"],
                title=item.get("title", "").strip(),
                company_name=item.get("company_name", "").strip() or "Unknown",
                description=item.get("description", ""),
                location=item.get("candidate_required_location"),
                remote=True,  # Remotive is remote-only by definition
                employment_type=item.get("job_type"),
                posted_at=_parse_date(item.get("publication_date")),
                tags=list(item.get("tags") or []),
                raw=item,
            )


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
