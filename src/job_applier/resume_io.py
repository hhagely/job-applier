"""PDF resume I/O: extraction + on-disk storage."""

from __future__ import annotations

import io
import re
import uuid
from pathlib import Path

from pypdf import PdfReader

from job_applier.config import settings


def extract_text(pdf_bytes: bytes) -> tuple[str, int]:
    """Return (text, page_count). Raises ValueError if the PDF is unreadable."""
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        # Iterating reader.pages and page.extract_text() can also raise on a
        # PDF that constructs but has a malformed content stream, so keep the
        # whole extraction inside the guard (else the caller gets a 500).
        pages = [page.extract_text() or "" for page in reader.pages]
        page_count = len(reader.pages)
    except Exception as exc:  # pypdf raises a variety of types
        raise ValueError(f"could not read PDF: {exc}") from exc

    text = _normalize("\n\n".join(pages))
    return text, page_count


def save_pdf(pdf_bytes: bytes, original_filename: str) -> Path:
    settings.resumes_dir.mkdir(parents=True, exist_ok=True)
    safe_stem = uuid.uuid4().hex
    suffix = Path(original_filename).suffix.lower() or ".pdf"
    path = settings.resumes_dir / f"{safe_stem}{suffix}"
    path.write_bytes(pdf_bytes)
    return path


def to_markdown(text: str) -> str:
    """Best-effort markdown rendering of extracted resume text.

    pypdf gives us plain text with paragraph-ish line breaks — not real structure.
    We wrap the whole thing in a fenced block plus a top-level H1 so it's at least
    legible in the UI. Real structured parsing comes later via Claude Code if the
    plain-text matcher isn't sharp enough.
    """
    return f"# Resume (extracted text)\n\n```\n{text.strip()}\n```\n"


_MULTI_BLANK = re.compile(r"\n{3,}")
_TRAILING_WS = re.compile(r"[ \t]+\n")
# C0 control chars that pypdf sometimes emits from malformed PDFs — NUL in
# particular is illegal in a subprocess argument (breaks the AI CLI call with
# "embedded null character") and renders as a broken glyph in the extracted-text
# panel. Keep tab (\x09) and newline (\x0a); drop the rest (plus DEL) so the stored
# text is clean at the source.
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _normalize(text: str) -> str:
    text = _CONTROL_CHARS.sub("", text)
    text = _TRAILING_WS.sub("\n", text)
    text = _MULTI_BLANK.sub("\n\n", text)
    return text.strip()
