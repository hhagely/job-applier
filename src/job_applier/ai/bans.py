"""ATS character ban list — the single encoding of the "no em dashes / smart
quotes / etc." rule enforced on generated draft markdown.

Some ATS / recruiter screens flag em-dash-heavy or smart-quote-laden text as
LLM-generated. The `/draft` slash command asks the model to avoid these; this
module is the server-side backstop that makes the rule a guarantee regardless of
which provider produced the text: sanitize the obvious substitutions, then assert
nothing banned remains.
"""

from __future__ import annotations

# Banned char -> ASCII replacement. Keep this list in sync with the ban section
# of `ai/prompts/draft.md` and `.claude/commands/draft.md`.
REPLACEMENTS: dict[str, str] = {
    "—": "-",  # em dash —
    "–": "-",  # en dash –
    "“": '"',  # left double smart quote “
    "”": '"',  # right double smart quote ”
    "‘": "'",  # left single smart quote ‘
    "’": "'",  # right single smart quote ’
    "…": "...",  # ellipsis …
    " ": " ",  # non-breaking space
    "​": "",  # zero-width space
    "‌": "",  # zero-width non-joiner
    "‍": "",  # zero-width joiner
    "﻿": "",  # BOM / zero-width no-break space
    "•": "-",  # bullet •
    "▪": "-",  # black small square ▪
    "▶": "-",  # play triangle ▶
    "★": "-",  # star ★
}

BANNED_CHARS: frozenset[str] = frozenset(REPLACEMENTS)


def sanitize(text: str) -> str:
    """Replace every banned character with its ASCII equivalent."""
    if not text:
        return text
    for bad, good in REPLACEMENTS.items():
        if bad in text:
            text = text.replace(bad, good)
    return text


def find_banned(text: str) -> list[str]:
    """Return the sorted distinct banned characters present in ``text``."""
    return sorted({ch for ch in text if ch in BANNED_CHARS})
