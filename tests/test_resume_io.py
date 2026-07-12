"""Resume text extraction normalization.

The extracted text is stored, shown in the "extracted text" panel, and fed to the
AI CLI as a prompt argument. Malformed PDFs make pypdf emit C0 control bytes; NUL
in particular is illegal in a subprocess argument (the AI call fails with "embedded
null character"). _normalize strips them at the source.
"""

from __future__ import annotations

import pytest

from job_applier import resume_io


def test_extract_text_raises_valueerror_when_page_extraction_fails(monkeypatch):
    # A PDF that *constructs* but has a malformed content stream raises while
    # iterating pages / extracting text — that must surface as ValueError (so the
    # endpoint returns 422), not escape as an opaque 500.
    class _BadPage:
        def extract_text(self):
            raise RuntimeError("malformed content stream")

    class _BadReader:
        def __init__(self, *args, **kwargs):
            self.pages = [_BadPage()]

    monkeypatch.setattr(resume_io, "PdfReader", _BadReader)
    with pytest.raises(ValueError, match="could not read PDF"):
        resume_io.extract_text(b"%PDF-1.4 ...")


def test_normalize_strips_null_and_control_chars():
    out = resume_io._normalize("Herb\x00 Hagely\x01\x1f Senior\x7f Engineer")
    assert "\x00" not in out
    assert out == "Herb Hagely Senior Engineer"


def test_normalize_keeps_tabs_and_newlines():
    out = resume_io._normalize("line1\n\tindented\nline3")
    assert out == "line1\n\tindented\nline3"


def test_normalize_collapses_blank_runs_and_trailing_ws():
    assert resume_io._normalize("a   \n\n\n\nb") == "a\n\nb"
