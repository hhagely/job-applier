"""Framework-free shared contracts.

``RawJob`` is the vocabulary that *sources* produce, *filters* evaluate, and
*ingest* persists — it belongs to none of them in particular, so it lives here.
The date parsers are shared source-adapter helpers with the same property. The
``AppSetting`` key names for the AI configuration live here for the same reason:
three routers read them and none owns them. This module deliberately has ZERO
intra-package dependencies (only stdlib), so both ``job_applier.sources`` and
``job_applier.filters`` can import it without forming the
``sources -> filters -> sources`` import cycle that used to require a
``TYPE_CHECKING`` guard in the filter and a lazy import in the source registry —
and so the API routers can import the setting keys without dragging anything in.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

# ---- AppSetting keys for the AI configuration ------------------------------
#
# The AI selection is persisted in the ``AppSetting`` key/value table and read
# from three routers (``api/ai.py``, ``api/deps.py``, ``api/drafts.py``). Defined
# once here because a hardcoded copy is invisible to a rename: ``get_setting``
# answers a missing key with its default, so a stale literal doesn't raise — it
# quietly reports "nothing configured" forever.

#: Selected AI CLI (``"claude"``, ``"gemini"``, ``"codex"``, ``"ollama"``).
AI_PROVIDER_KEY = "ai_provider"

#: Override for the baseline (bulk) scoring model. When unset, the resolver falls
#: back to the provider's built-in lighter default, then the generation model.
AI_SCORING_MODEL_KEY = "ai_scoring_model"

#: Prefix for the per-provider generation model — see ``ai_model_key``.
AI_MODEL_KEY_PREFIX = "ai_model:"

#: Pre-namespacing key: one global generation model shared by every provider.
#: Read-only now, and only for ``LEGACY_AI_MODEL_PROVIDER`` (see ``ai_model_key``).
AI_MODEL_KEY_LEGACY = "ai_model"

#: The provider a legacy ``ai_model`` value belongs to. Settings only ever
#: rendered that input for Ollama, so that is who typed it.
LEGACY_AI_MODEL_PROVIDER = "ollama"


# ---- AppSetting keys for user preferences ----------------------------------

#: Days of silence after which an application is offered up as ghosted on
#: /followups. Stored as a decimal string like every other ``AppSetting`` value.
GHOSTED_AFTER_DAYS_KEY = "ghosted_after_days"

#: Fallback when the key was never set (or holds something unparseable).
DEFAULT_GHOSTED_AFTER_DAYS = 45

#: Floor is the default follow-up interval: calling an application ghosted before
#: its first nudge is even due would be nonsense. Ceiling is a year, which is well
#: past the point any employer is still deciding.
MIN_GHOSTED_AFTER_DAYS = 7
MAX_GHOSTED_AFTER_DAYS = 365


def ai_model_key(provider: str) -> str:
    """Setting key holding ``provider``'s generation model (drafting, suggest-roles,
    tailored re-scoring, the Test round-trip).

    Namespaced per provider on purpose: a model name is only meaningful to the CLI
    it was typed for, so one shared key let a value chosen for one CLI be handed to
    the next one selected — ``claude -p ... --model llama3.1`` exits non-zero and
    every generation flow breaks at once.
    """
    return f"{AI_MODEL_KEY_PREFIX}{provider}"


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
