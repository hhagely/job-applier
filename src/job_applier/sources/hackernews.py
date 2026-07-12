"""Hacker News "Who is hiring" source.

Each month ``whoishiring`` posts a thread; top-level comments are individual
job posts. We use the Algolia HN API:

  - List recent ``whoishiring`` submissions:
      https://hn.algolia.com/api/v1/search_by_date?tags=story,author_whoishiring
  - Fetch a thread (with all children) by id:
      https://hn.algolia.com/api/v1/items/{id}

Each top-level comment has loose but conventional structure:

    Company | Location | REMOTE/ONSITE/HYBRID | Full-time | <link>
    <description body, optional second URL, etc.>

We extract company/location/remote-flag from the first non-empty line and use
the remaining text as the description. Posts that don't match the convention
are still emitted with ``company_name="Unknown"`` so the user can review them
manually rather than silently dropping signal.
"""

from __future__ import annotations

import html
import logging
import re
from collections.abc import Iterable
from datetime import datetime, timezone

import httpx

from job_applier.sources.base import RawJob, parse_iso_date

log = logging.getLogger(__name__)

SEARCH_URL = "https://hn.algolia.com/api/v1/search_by_date"
ITEM_URL = "https://hn.algolia.com/api/v1/items/{id}"

# How many recent monthly threads to ingest. The current month will dominate.
# Two months of overlap helps catch edits / late additions and roles still open.
MAX_THREADS = 2

REMOTE_HINT = re.compile(r"\bremote\b", re.IGNORECASE)
ONSITE_HINT = re.compile(r"\b(on[\s-]?site|hybrid|in[\s-]?office)\b", re.IGNORECASE)
TAG_RE = re.compile(r"<[^>]+>")
URL_RE = re.compile(r"https?://[^\s<>\"']+")


class HackerNewsHiringSource:
    name = "hackernews"

    def fetch(self) -> Iterable[RawJob]:
        try:
            with httpx.Client(timeout=30.0, follow_redirects=True) as client:
                threads = _find_hiring_threads(client, MAX_THREADS)
                for thread_id, thread_created in threads:
                    try:
                        resp = client.get(ITEM_URL.format(id=thread_id))
                        resp.raise_for_status()
                        thread = resp.json()
                    except (httpx.HTTPError, ValueError) as e:
                        log.warning("hackernews thread %s fetch failed: %s", thread_id, e)
                        continue
                    yield from _normalize_thread(thread, thread_created)
        except (httpx.HTTPError, ValueError) as e:
            log.warning("hackernews search failed: %s", e)
            return


def _find_hiring_threads(
    client: httpx.Client, limit: int
) -> list[tuple[int, datetime | None]]:
    resp = client.get(
        SEARCH_URL,
        params={
            "tags": "story,author_whoishiring",
            "hitsPerPage": 20,
        },
    )
    resp.raise_for_status()
    data = resp.json()
    hits = data.get("hits", []) if isinstance(data, dict) else []
    threads: list[tuple[int, datetime | None]] = []
    for h in hits:
        title = (h.get("title") or "").lower()
        if "who is hiring" not in title:
            continue
        try:
            tid = int(h["objectID"])
        except (KeyError, ValueError, TypeError):
            continue
        created = parse_iso_date(h.get("created_at"))
        threads.append((tid, created))
        if len(threads) >= limit:
            break
    return threads


def _normalize_thread(
    thread: dict, thread_created: datetime | None
) -> Iterable[RawJob]:
    if not isinstance(thread, dict):
        return
    thread_id = thread.get("id")
    for child in thread.get("children") or []:
        if not isinstance(child, dict):
            continue
        text_html = child.get("text") or ""
        if not text_html:
            continue
        comment_id = child.get("id")
        if comment_id is None:
            continue

        company, header_title, location, remote = _parse_header(text_html)
        plain = _html_to_text(text_html)
        url = _first_url(plain) or f"https://news.ycombinator.com/item?id={comment_id}"
        title = header_title or _derive_title(company, plain) or "(see description)"
        posted_at = _parse_unix(child.get("created_at_i")) or thread_created

        yield RawJob(
            source="hackernews",
            source_id=str(comment_id),
            url=url,
            title=title,
            company_name=company or "Unknown",
            description=plain,
            location=location,
            remote=remote,
            employment_type=None,
            posted_at=posted_at,
            tags=["hn-whoishiring"],
            raw={
                "thread_id": thread_id,
                "comment_id": comment_id,
                "author": child.get("author"),
                "text_html": text_html,
            },
        )


_EMPTYPE_RE = re.compile(r"(?i)^(full[-\s]?time|part[-\s]?time|contract|intern)$")
_COMP_RE = re.compile(r"^[\$€£]")  # "$180k", "$180-200K + equity"
_TITLE_KEYWORDS = re.compile(
    r"\b(engineer|developer|architect|sde|swe|designer|manager|director|"
    r"lead|principal|staff|founder|cto|ceo|head|scientist|researcher|analyst)\b",
    re.IGNORECASE,
)


def _classify_part(part: str) -> str:
    """Classify a `|`-delimited header segment by what it looks like."""
    if URL_RE.search(part):
        return "url"
    if REMOTE_HINT.fullmatch(part) or ONSITE_HINT.fullmatch(part):
        return "workplace"
    if _EMPTYPE_RE.match(part):
        return "emptype"
    if _COMP_RE.search(part):
        return "comp"
    if _TITLE_KEYWORDS.search(part):
        return "title"
    return "other"


def _parse_header(
    text_html: str,
) -> tuple[str | None, str | None, str | None, bool]:
    """Pull company / title / location / remote-flag from the first line.

    Conventional format is loose: ``Company | Title | Location | TYPE | $$$``,
    but pieces appear in any order. We classify each piece by content shape
    (title keywords, compensation symbol, REMOTE/ONSITE, employment type, URL)
    and assign by classification, not position.
    """
    plain = _html_to_text(text_html)
    first_line = next((ln for ln in plain.splitlines() if ln.strip()), "")
    parts = [p.strip() for p in first_line.split("|") if p.strip()]
    if not parts:
        return None, None, None, _looks_remote(text_html)

    company = parts[0] or None
    title: str | None = None
    location: str | None = None
    for p in parts[1:]:
        kind = _classify_part(p)
        if kind == "title" and title is None:
            title = p
        elif kind == "other" and location is None:
            location = p

    remote = bool(REMOTE_HINT.search(first_line))
    if ONSITE_HINT.search(first_line) and not remote:
        remote = False
    return company, title, location, remote


def _derive_title(company: str | None, plain: str) -> str | None:
    """Try to find a job title in the body. Fall back to company-prefixed marker."""
    # Many posts have a section like "We're hiring: Senior Backend Engineer"
    # or just include role names in headers. Best-effort regex.
    m = re.search(
        r"\b(?:hiring|seeking|looking for)[:\s]+([A-Z][\w\s\-/&,]{4,80})",
        plain,
    )
    if m:
        return m.group(1).strip().rstrip(".,;:")
    if company:
        return f"{company} (HN — see description)"
    return None


def _first_url(text: str) -> str | None:
    m = URL_RE.search(text)
    return m.group(0) if m else None


def _html_to_text(s: str) -> str:
    if not s:
        return ""
    # HN comments use <p> for paragraphs and <a href="...">...</a> for links.
    # Replace block tags with newlines, drop the rest, then unescape entities.
    s = re.sub(r"<\s*/?p\s*>", "\n", s, flags=re.IGNORECASE)
    s = re.sub(r"<\s*br\s*/?\s*>", "\n", s, flags=re.IGNORECASE)
    s = TAG_RE.sub("", s)
    return html.unescape(s).strip()


def _looks_remote(html_text: str) -> bool:
    return bool(REMOTE_HINT.search(html_text)) and not ONSITE_HINT.search(html_text)


def _parse_unix(value: int | None) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc)
    except (ValueError, OSError, TypeError):
        return None


__all__ = ["HackerNewsHiringSource", "MAX_THREADS"]
