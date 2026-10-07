"""Tailored-resume + cover-letter drafts: storage and print-HTML rendering.

Drafts live on disk under ``settings.applications_dir/profile-<id>/<job_id>/``
(per profile, so two people's tailored drafts for the same job never collide):

    resume.md
    resume.pdf
    cover_letter.md
    cover_letter.pdf

Markdown is the master format the slash command / in-app drafting writes.
PDFs are produced by a browser engine (headless Chromium in dev, Electron's
WebView in the packaged app) that prints the standalone HTML this module builds
from the markdown. This module no longer renders PDFs itself — markdown
persistence (`save_markdown`) and PDF writing (`render_pdf`) are separate so the
caller that owns a browser engine drives the actual print. See
``src/job_applier/pdf.py`` for the dev/standalone print driver.
"""

from __future__ import annotations

import logging
import shutil
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from markdown_it import MarkdownIt
from markdown_it.renderer import RendererHTML

from job_applier.config import settings

DraftKind = Literal["resume", "cover_letter"]

_FILES: dict[DraftKind, tuple[str, str]] = {
    "resume": ("resume.md", "resume.pdf"),
    "cover_letter": ("cover_letter.md", "cover_letter.pdf"),
}

_PRINT_CSS = """
@page { size: Letter; margin: 0.7in 0.75in; }
html { font-family: 'Helvetica', 'Arial', sans-serif; font-size: 10.5pt; color: #111; }
body { line-height: 1.4; }
h1 { font-size: 18pt; margin: 0 0 0.15em; }
h2 { font-size: 12pt; margin: 1em 0 0.3em; text-transform: uppercase;
     letter-spacing: 0.04em; border-bottom: 1px solid #999; padding-bottom: 2px; }
h3 { font-size: 11pt; margin: 0.7em 0 0.2em; }
p, li { margin: 0.25em 0; }
ul { padding-left: 1.1em; margin: 0.3em 0; }
a { color: #2257a5; text-decoration: none; }
hr { border: 0; border-top: 1px solid #ccc; margin: 0.7em 0; }
strong { font-weight: 600; }
"""

# Cover letters are business letters, not resumes: roomier margins, a
# lighter name header, generous paragraph spacing, and a contact line that
# sits just under the name. Rendered with soft-break => <br> (see _md_letter)
# so the signature block ("Sincerely," / name) keeps its line breaks.
_COVER_LETTER_CSS = """
@page { size: Letter; margin: 1in 1in; }
html { font-family: 'Helvetica', 'Arial', sans-serif; font-size: 11pt; color: #111; }
body { line-height: 1.5; }
h1 { font-size: 16pt; font-weight: 600; margin: 0 0 0.1em; }
h1 + p { margin-top: 0; color: #555; font-size: 10pt; }
p { margin: 0 0 0.85em; }
a { color: #2257a5; text-decoration: none; }
strong { font-weight: 600; }
"""


class _NoAnchorRenderer(RendererHTML):
    """HTML renderer that emits link *text* but never an ``<a href>`` element.

    Last gate on the guarantee ``ai/bans.strip_exfil_vectors`` sets up: a draft is
    physically sent to an employer, and job descriptions are untrusted scraped text,
    so a prompt-injected draft must not reach the recipient carrying a clickable
    tracking link. ``strip_exfil_vectors`` flattens links to plain text at the
    ``save_markdown`` choke point; dropping the anchor tokens here means the print
    HTML holds no clickable link even if markdown reaches this renderer by some other
    path (a future writer that bypasses the choke point, or a link shape the strip
    regexes don't cover). Note the risk this closes is a *human* clicking the link in
    the delivered PDF, not a fetch during rendering: ``<a href>`` is not fetched at
    print time, and ``pdf.py``'s route guard blocks every subresource anyway.
    """

    def link_open(self, tokens, idx, options, env) -> str:
        return ""

    def link_close(self, tokens, idx, options, env) -> str:
        return ""


# Soft newlines become <br> so single-line-break constructs keep their layout:
# on the resume, each Skills category and the three-line per-role header sit on their
# own source line with no blank line between them, and would otherwise collapse into
# one run-on paragraph; on the letter, the salutation/signature lines.
#
# `linkify` is deliberately NOT enabled: it would turn the bare URLs that
# `strip_exfil_vectors` deliberately produced (it flattens `[text](url)` to plain
# text precisely so the URL is not clickable) straight back into `<a href>` links,
# undoing the strip. Do not re-enable it as a convenience. `_NoAnchorRenderer` is the
# belt to that braces, so the outcome no longer depends on this option's value.
_MD_OPTIONS = {"html": False, "breaks": True}

_md = MarkdownIt(
    "commonmark", _MD_OPTIONS, renderer_cls=_NoAnchorRenderer
).enable("table")
_md_letter = MarkdownIt("commonmark", _MD_OPTIONS, renderer_cls=_NoAnchorRenderer)

_RENDER: dict[DraftKind, tuple[MarkdownIt, str]] = {
    "resume": (_md, _PRINT_CSS),
    "cover_letter": (_md_letter, _COVER_LETTER_CSS),
}


@dataclass(frozen=True)
class DraftStatus:
    job_id: int
    has_resume_md: bool
    has_resume_pdf: bool
    has_cover_letter_md: bool
    has_cover_letter_pdf: bool
    updated_at: datetime | None


log = logging.getLogger(__name__)


def profile_dir(profile_id: int) -> Path:
    """Where one profile's drafts live: ``<applications>/profile-<id>/``."""
    return settings.applications_dir / f"profile-{profile_id}"


def draft_dir(job_id: int, *, profile_id: int) -> Path:
    return profile_dir(profile_id) / str(job_id)


def legacy_draft_dirs() -> list[Path]:
    """Pre-profile draft folders (``<applications>/<job_id>/``) still to move.
    The new layout's ``profile-`` prefix can never match. Runs at startup, so an
    unreadable folder is logged and reads as "none" rather than stopping boot."""
    root = settings.applications_dir
    try:
        if not root.is_dir():
            return []
        return [e for e in root.iterdir() if e.is_dir() and e.name.isdigit()]
    except OSError as exc:
        log.warning("couldn't list legacy draft folders in %s: %s", root, exc)
        return []


def set_aside_profile_drafts(profile_id: int) -> Path | None:
    """Rename a profile's drafts folder out of the way before the profile is
    deleted, so a later profile that reuses the id never finds them. Raises
    ``OSError`` when it can't (on Windows, a PDF open in a viewer), before
    anything has been deleted. Returns the new path, or None when there were no
    drafts."""
    folder = profile_dir(profile_id)
    if not folder.exists():
        return None
    doomed = folder.with_name(f"{folder.name}.deleted-{uuid.uuid4().hex[:8]}")
    folder.rename(doomed)
    return doomed


def remove_set_aside(path: Path) -> None:
    """Best-effort removal of a folder from ``set_aside_profile_drafts``; what
    can't be removed is logged and left (its name is unique, so it's inert)."""
    shutil.rmtree(
        path,
        onexc=lambda _fn, p, exc: log.warning("couldn't remove deleted draft %s: %s", p, exc),
    )


def move_legacy_draft_dirs(profile_id: int) -> int:
    """One-time move of pre-profile drafts (``<applications>/<job_id>/``) under
    ``profile-<profile_id>/``. Returns how many directories moved.

    Idempotent: only top-level all-digit directories are legacy (the new layout's
    ``profile-`` prefix can never match), and a target that already exists is left
    alone rather than overwritten. A rename within one directory tree is atomic per
    directory, so an interrupted run just finishes on the next start.
    """
    dest_root = profile_dir(profile_id)
    moved = 0
    for entry in legacy_draft_dirs():
        dest = dest_root / entry.name
        if dest.exists():
            continue
        try:
            dest_root.mkdir(parents=True, exist_ok=True)
            entry.rename(dest)
        except OSError as exc:
            # Best effort at startup: a file open in a viewer blocks the rename
            # on Windows. Leave it for the next start rather than refuse to boot.
            log.warning("couldn't move legacy draft folder %s: %s", entry, exc)
            continue
        moved += 1
    return moved


def render_print_html(md_text: str, kind: DraftKind) -> str:
    """Assemble the standalone, print-ready HTML document for a draft.

    Pure function: markdown -> HTML body with the kind's CSS profile inlined in a
    ``<style>`` tag and the ``@page`` rules driving size/margins. A browser engine
    prints this to PDF (headless Chromium in dev, Electron in the packaged app).
    """
    renderer, css = _RENDER[kind]
    html_body = renderer.render(md_text)
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<style>{css}</style></head><body>{html_body}</body></html>"
    )


def _clean_draft(md: str) -> str:
    """Apply the char bans + exfil-vector strip to draft markdown. This is the single
    choke point every draft-write passes through (AI generation *and* the manual
    edit/re-render endpoint), so no draft is ever persisted with a tracking image, a
    clickable tracking link, or a banned ATS character regardless of its origin."""
    # Lazy import: bans lives under the ai package; importing it at module load would
    # couple this low-level storage module to the AI layer's import graph.
    from job_applier.ai import bans

    return bans.strip_exfil_vectors(bans.sanitize(md))


def save_markdown(
    job_id: int,
    resume_md: str | None,
    cover_letter_md: str | None,
    *,
    profile_id: int,
) -> DraftStatus:
    """Write any provided markdown to disk (no PDF). Returns the latest status.

    Markdown is sanitized here (char bans + exfil-vector strip) so the guarantee holds
    for every writer. Kept independent of PDF rendering so drafts persist even when no
    browser engine is available, and so Electron can drive the print separately later.
    """
    d = draft_dir(job_id, profile_id=profile_id)
    d.mkdir(parents=True, exist_ok=True)

    if resume_md is not None:
        (d / _FILES["resume"][0]).write_text(_clean_draft(resume_md), encoding="utf-8")
    if cover_letter_md is not None:
        (d / _FILES["cover_letter"][0]).write_text(
            _clean_draft(cover_letter_md), encoding="utf-8"
        )

    return get_status(job_id, profile_id=profile_id)


def render_pdf(job_id: int, kind: DraftKind, pdf_bytes: bytes, *, profile_id: int) -> None:
    """Persist caller-produced PDF bytes for a draft kind."""
    d = draft_dir(job_id, profile_id=profile_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / _FILES[kind][1]).write_bytes(pdf_bytes)


def existing_markdown_kinds(job_id: int, *, profile_id: int) -> list[DraftKind]:
    """Draft kinds that currently have a saved ``.md`` on disk."""
    d = draft_dir(job_id, profile_id=profile_id)
    kinds: list[DraftKind] = []
    for kind, (md_name, _pdf_name) in _FILES.items():
        if (d / md_name).exists():
            kinds.append(kind)
    return kinds


def get_status(job_id: int, *, profile_id: int) -> DraftStatus:
    d = draft_dir(job_id, profile_id=profile_id)
    paths = {
        "resume_md": d / "resume.md",
        "resume_pdf": d / "resume.pdf",
        "cover_letter_md": d / "cover_letter.md",
        "cover_letter_pdf": d / "cover_letter.pdf",
    }
    mtimes = [p.stat().st_mtime for p in paths.values() if p.exists()]
    updated = (
        datetime.fromtimestamp(max(mtimes), tz=timezone.utc) if mtimes else None
    )
    return DraftStatus(
        job_id=job_id,
        has_resume_md=paths["resume_md"].exists(),
        has_resume_pdf=paths["resume_pdf"].exists(),
        has_cover_letter_md=paths["cover_letter_md"].exists(),
        has_cover_letter_pdf=paths["cover_letter_pdf"].exists(),
        updated_at=updated,
    )


def read_markdown(job_id: int, kind: DraftKind, *, profile_id: int) -> str | None:
    md_path = draft_dir(job_id, profile_id=profile_id) / _FILES[kind][0]
    return md_path.read_text(encoding="utf-8") if md_path.exists() else None


def pdf_path(job_id: int, kind: DraftKind, *, profile_id: int) -> Path:
    return draft_dir(job_id, profile_id=profile_id) / _FILES[kind][1]
