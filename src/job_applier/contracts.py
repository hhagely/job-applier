"""Framework-free shared contracts for the ingest pipeline.

``RawJob`` is the vocabulary that *sources* produce, *filters* evaluate, and
*ingest* persists — it belongs to none of them in particular, so it lives here.
The date parsers are shared source-adapter helpers with the same property. This
module deliberately has ZERO intra-package dependencies (only stdlib), so both
``job_applier.sources`` and ``job_applier.filters`` can import it without forming
the ``sources -> filters -> sources`` import cycle that used to require a
``TYPE_CHECKING`` guard in the filter and a lazy import in the source registry.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

_HTML_BLOCK_TAG = re.compile(r"<\s*/?(p|div|li|h[1-6])\s*>", re.IGNORECASE)
_HTML_BR_TAG = re.compile(r"<\s*br\s*/?\s*>", re.IGNORECASE)
_HTML_ANY_TAG = re.compile(r"<[^>]+>")
_HTML_BLANK_RUN = re.compile(r"\n{3,}")


def html_to_text(s: Optional[str]) -> str:
    """Flatten scraped HTML to readable plain text.

    Block tags (``p``/``div``/``li``/``h1``-``h6``) and ``<br>`` become newlines,
    any remaining tags are dropped, HTML entities are unescaped, and runs of blank
    lines are collapsed. Shared by the scoring/drafting prompt builders (which feed
    the JD to the LLM) and the HackerNews adapter. This is the *readable* flattener;
    ingest's SimHash tokenizer keeps its own tag-to-space cleaner on purpose.
    """
    if not s:
        return ""
    s = _HTML_BLOCK_TAG.sub("\n", s)
    s = _HTML_BR_TAG.sub("\n", s)
    s = _HTML_ANY_TAG.sub("", s)
    s = html.unescape(s)
    return _HTML_BLANK_RUN.sub("\n\n", s).strip()


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
