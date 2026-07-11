"""Tailored-resume + cover-letter drafts: storage and print-HTML rendering.

Drafts live on disk under ``settings.applications_dir/<job_id>/``:

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

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from markdown_it import MarkdownIt

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

# Soft newlines become <br> so single-line-break constructs keep their layout:
# on the resume, each Skills category and the three-line per-role header sit on their
# own source line with no blank line between them, and would otherwise collapse into
# one run-on paragraph; on the letter, the salutation/signature lines.
_md = MarkdownIt(
    "commonmark", {"linkify": True, "html": False, "breaks": True}
).enable("table")
_md_letter = MarkdownIt(
    "commonmark", {"linkify": True, "html": False, "breaks": True}
)

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


def draft_dir(job_id: int) -> Path:
    return settings.applications_dir / str(job_id)


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


def save_markdown(
    job_id: int, resume_md: str | None, cover_letter_md: str | None
) -> DraftStatus:
    """Write any provided markdown to disk (no PDF). Returns the latest status.

    Kept independent of PDF rendering so drafts persist even when no browser
    engine is available, and so Electron can drive the print separately later.
    """
    d = draft_dir(job_id)
    d.mkdir(parents=True, exist_ok=True)

    if resume_md is not None:
        (d / _FILES["resume"][0]).write_text(resume_md, encoding="utf-8")
    if cover_letter_md is not None:
        (d / _FILES["cover_letter"][0]).write_text(cover_letter_md, encoding="utf-8")

    return get_status(job_id)


def render_pdf(job_id: int, kind: DraftKind, pdf_bytes: bytes) -> None:
    """Persist caller-produced PDF bytes for a draft kind."""
    d = draft_dir(job_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / _FILES[kind][1]).write_bytes(pdf_bytes)


def existing_markdown_kinds(job_id: int) -> list[DraftKind]:
    """Draft kinds that currently have a saved ``.md`` on disk."""
    d = draft_dir(job_id)
    kinds: list[DraftKind] = []
    for kind, (md_name, _pdf_name) in _FILES.items():
        if (d / md_name).exists():
            kinds.append(kind)
    return kinds


def get_status(job_id: int) -> DraftStatus:
    d = draft_dir(job_id)
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


def read_markdown(job_id: int, kind: DraftKind) -> str | None:
    md_path = draft_dir(job_id) / _FILES[kind][0]
    return md_path.read_text(encoding="utf-8") if md_path.exists() else None


def pdf_path(job_id: int, kind: DraftKind) -> Path:
    return draft_dir(job_id) / _FILES[kind][1]
