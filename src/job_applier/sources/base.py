"""Source-adapter contract.

The shared ``RawJob`` vocabulary and the date-parsing helpers now live in
``job_applier.contracts`` (dependency-free, so ``filters`` can import them without
a cycle). They are re-exported here so the adapters can keep importing everything
they need from one place — ``from job_applier.sources.base import RawJob, ...``.
"""

import re
from collections.abc import Iterable
from typing import Protocol

from job_applier.contracts import RawJob, parse_date_multi, parse_iso_date

__all__ = [
    "RawJob",
    "SourceAdapter",
    "TITLE_GATE",
    "looks_remote",
    "parse_date_multi",
    "parse_iso_date",
]


def looks_remote(*texts: str | None) -> bool:
    """True if any fragment contains "remote" (case-insensitive).

    The plain-substring half of the remote check nearly every adapter does. The
    per-source parts that vary — an explicit ``isRemote``/``workplaceType`` flag,
    a hybrid/on-site exclusion, or a term list beyond just "remote" — stay in the
    adapter; this only collapses the repeated ``"remote" in x.lower()`` idiom.
    """
    return any("remote" in (t or "").lower() for t in texts)

# Senior + engineering title gate — a cheap pre-filter shared by the Workday and
# Oracle adapters, which apply it before an expensive per-posting detail fetch.
# Anything that passes still runs the full filter pipeline downstream, so the
# gate only has to be roughly right. Kept here (not per-adapter) so the two
# copies can't drift.
TITLE_GATE = re.compile(
    r"\b(senior|sr\.?|staff|principal|lead|architect|distinguished|head\s+of)\b.*?"
    r"\b(engineer|developer|architect|sde|swe)\b",
    re.IGNORECASE,
)


class SourceAdapter(Protocol):
    name: str

    def fetch(self) -> Iterable[RawJob]: ...
