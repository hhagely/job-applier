from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from job_applier import drafts, pdf
from job_applier.api.app import app
from job_applier.config import settings
from job_applier.models import JobPosting
from job_applier.models.db import FilterStatus, get_session

RESUME_MD = "# Jane Dev\n\n## Experience\n\n- Built **TypeScript** services on Node.js\n"
COVER_MD = "# Jane Dev\njane@example.com\n\nDear Hiring Manager,\n\nI am excited.\n\nSincerely,\nJane\n"


@pytest.fixture
def client(tmp_path, monkeypatch):
    # Drafts must land in a throwaway dir, never the real applications/.
    monkeypatch.setattr(settings, "applications_dir", tmp_path)

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    def _session_dep():
        with Session(engine) as s:
            yield s

    app.dependency_overrides[get_session] = _session_dep
    with TestClient(app) as c:
        yield c, engine
    app.dependency_overrides.clear()


def _seed_job(engine) -> int:
    with Session(engine) as s:
        job = JobPosting(
            source="test",
            source_id="t-1",
            url="https://example.com/jobs/1",
            title="Senior Engineer",
            description="TypeScript role.",
            dedupe_hash="h1",
            filter_status=FilterStatus.passed,
        )
        s.add(job)
        s.commit()
        s.refresh(job)
        return job.id


# --- render_print_html (pure) ---------------------------------------------


def test_render_print_html_resume_uses_resume_css():
    html = drafts.render_print_html(RESUME_MD, "resume")
    assert html.startswith("<!doctype html>")
    assert "margin: 0.7in 0.75in" in html  # _PRINT_CSS @page marker
    assert "<h1>Jane Dev</h1>" in html
    assert "<strong>TypeScript</strong>" in html


def test_render_print_html_cover_letter_uses_cover_css_and_soft_breaks():
    html = drafts.render_print_html(COVER_MD, "cover_letter")
    assert "margin: 1in 1in" in html  # _COVER_LETTER_CSS @page marker
    # Cover letters render soft newlines as <br> (signature block stays intact).
    assert "<br" in html


# --- print.html endpoint ---------------------------------------------------


def test_print_html_endpoint_serves_document(client):
    c, engine = client
    job_id = _seed_job(engine)
    drafts.save_markdown(job_id, RESUME_MD, None)

    resp = c.get(f"/api/jobs/{job_id}/draft/resume/print.html")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    assert "margin: 0.7in 0.75in" in resp.text
    assert "Jane Dev" in resp.text


def test_print_html_404_when_no_markdown(client):
    c, engine = client
    job_id = _seed_job(engine)
    resp = c.get(f"/api/jobs/{job_id}/draft/resume/print.html")
    assert resp.status_code == 404


def test_print_html_404_for_unknown_kind(client):
    c, engine = client
    job_id = _seed_job(engine)
    drafts.save_markdown(job_id, RESUME_MD, None)
    resp = c.get(f"/api/jobs/{job_id}/draft/bogus/print.html")
    assert resp.status_code == 404


def test_print_html_404_for_missing_job(client):
    c, _engine = client
    resp = c.get("/api/jobs/9999/draft/resume/print.html")
    assert resp.status_code == 404


# --- save/render endpoint wiring (PDF driver stubbed) ----------------------


def test_save_draft_saves_md_and_writes_pdf(client, monkeypatch):
    c, engine = client
    job_id = _seed_job(engine)

    calls: list[str] = []

    def _fake_render(url: str) -> bytes:
        calls.append(url)
        return b"%PDF-1.7 fake"

    monkeypatch.setattr(pdf, "render_to_pdf", _fake_render)

    resp = c.post(
        f"/api/jobs/{job_id}/draft",
        json={"resume_md": RESUME_MD, "cover_letter_md": COVER_MD},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["has_resume_md"] and body["has_resume_pdf"]
    assert body["has_cover_letter_md"] and body["has_cover_letter_pdf"]

    # Same files land on disk as before, via the same endpoint.
    assert drafts.pdf_path(job_id, "resume").read_bytes() == b"%PDF-1.7 fake"
    assert drafts.pdf_path(job_id, "cover_letter").read_bytes() == b"%PDF-1.7 fake"
    # Driver was pointed at the print-HTML endpoint for each kind.
    assert any(u.endswith(f"/api/jobs/{job_id}/draft/resume/print.html") for u in calls)
    assert any(
        u.endswith(f"/api/jobs/{job_id}/draft/cover_letter/print.html") for u in calls
    )


def test_render_draft_endpoint_rerenders_existing_markdown(client, monkeypatch):
    c, engine = client
    job_id = _seed_job(engine)
    drafts.save_markdown(job_id, RESUME_MD, None)
    monkeypatch.setattr(pdf, "render_to_pdf", lambda url: b"%PDF-1.7 fake")

    resp = c.post(f"/api/jobs/{job_id}/draft/render")
    assert resp.status_code == 200
    assert resp.json()["has_resume_pdf"] is True
    assert drafts.pdf_path(job_id, "resume").exists()


def test_render_draft_404_without_markdown(client):
    c, engine = client
    job_id = _seed_job(engine)
    resp = c.post(f"/api/jobs/{job_id}/draft/render")
    assert resp.status_code == 404


def test_save_draft_keeps_markdown_when_renderer_unavailable(client, monkeypatch):
    c, engine = client
    job_id = _seed_job(engine)

    def _boom(url: str) -> bytes:
        raise pdf.PdfRendererUnavailable("no chromium")

    monkeypatch.setattr(pdf, "render_to_pdf", _boom)

    resp = c.post(f"/api/jobs/{job_id}/draft", json={"resume_md": RESUME_MD})
    assert resp.status_code == 503
    # Markdown is persisted even though the PDF step failed.
    assert drafts.read_markdown(job_id, "resume") == RESUME_MD
    assert not drafts.pdf_path(job_id, "resume").exists()


# --- real browser render (gated) -------------------------------------------


@pytest.fixture
def chromium_available():
    """Skip cleanly when Playwright/Chromium isn't installed."""
    try:
        # A trivial render proves the engine is present without needing a server.
        pdf.render_to_pdf("data:text/html,<html><body>ok</body></html>")
    except pdf.PdfRendererUnavailable as exc:
        pytest.skip(f"headless Chromium unavailable: {exc}")


@pytest.mark.browser
def test_draft_save_then_render_produces_real_pdf(
    tmp_path, monkeypatch, chromium_available
):
    """End-to-end through real headless Chromium on the same print HTML the
    endpoint serves: a non-empty %PDF lands at pdf_path."""
    monkeypatch.setattr(settings, "applications_dir", tmp_path)
    job_id = 42
    drafts.save_markdown(job_id, RESUME_MD, None)

    # Render the exact print HTML the endpoint would serve, via a file URL so no
    # running server is required, then persist through the same render_pdf seam.
    html = drafts.render_print_html(RESUME_MD, "resume")
    html_file = tmp_path / "print.html"
    html_file.write_text(html, encoding="utf-8")
    pdf_bytes = pdf.render_to_pdf(html_file.as_uri())
    drafts.render_pdf(job_id, "resume", pdf_bytes)

    out = drafts.pdf_path(job_id, "resume")
    assert out.exists()
    assert out.read_bytes().startswith(b"%PDF")
    assert out.stat().st_size > 1000
