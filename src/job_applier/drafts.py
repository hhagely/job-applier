"""Tailored-resume + cover-letter drafts: storage and PDF rendering.

Drafts live on disk under ``settings.applications_dir/<job_id>/``:

    resume.md
    resume.pdf
    cover_letter.md
    cover_letter.pdf

Markdown is the master format the slash command writes; PDFs are rendered
server-side via markdown-it-py + weasyprint so we keep all LLM work in the
user's Claude Code session.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from markdown_it import MarkdownIt
from weasyprint import CSS, HTML

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

_md = MarkdownIt("commonmark", {"linkify": True, "html": False}).enable("table")


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


def _markdown_to_pdf_bytes(md_text: str) -> bytes:
    html_body = _md.render(md_text)
    full = f"<!doctype html><html><head><meta charset='utf-8'></head><body>{html_body}</body></html>"
    return HTML(string=full).write_pdf(stylesheets=[CSS(string=_PRINT_CSS)])


def save_and_render(
    job_id: int, resume_md: str | None, cover_letter_md: str | None
) -> DraftStatus:
    """Write any provided markdown to disk and render its PDF. Returns latest status."""
    d = draft_dir(job_id)
    d.mkdir(parents=True, exist_ok=True)

    if resume_md is not None:
        _write_pair(d, "resume", resume_md)
    if cover_letter_md is not None:
        _write_pair(d, "cover_letter", cover_letter_md)

    return get_status(job_id)


def render_existing(job_id: int) -> DraftStatus:
    """Re-render PDFs from whichever .md files exist on disk."""
    d = draft_dir(job_id)
    for kind, (md_name, pdf_name) in _FILES.items():
        md_path = d / md_name
        if md_path.exists():
            pdf_bytes = _markdown_to_pdf_bytes(md_path.read_text(encoding="utf-8"))
            (d / pdf_name).write_bytes(pdf_bytes)
    return get_status(job_id)


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


def _write_pair(d: Path, kind: DraftKind, md_text: str) -> None:
    md_name, pdf_name = _FILES[kind]
    (d / md_name).write_text(md_text, encoding="utf-8")
    (d / pdf_name).write_bytes(_markdown_to_pdf_bytes(md_text))
