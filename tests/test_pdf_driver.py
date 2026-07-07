from __future__ import annotations

import importlib.util
import pathlib

import httpx
import pytest

from job_applier import pdf

ENTRY = pathlib.Path(__file__).resolve().parents[1] / "desktop" / "sidecar" / "entry.py"


def _load_entry():
    spec = importlib.util.spec_from_file_location("sidecar_entry", ENTRY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---- PDF transport selection ----------------------------------------------


def test_render_via_service_when_env_set(monkeypatch):
    monkeypatch.setenv(pdf.PDF_SERVICE_ENV, "http://127.0.0.1:9999/")
    captured = {}

    class _Resp:
        content = b"%PDF-1.7 from-electron"

        def raise_for_status(self):
            pass

    def _fake_post(url, json, timeout):
        captured["url"] = url
        captured["json"] = json
        return _Resp()

    monkeypatch.setattr(httpx, "post", _fake_post)

    out = pdf.render_to_pdf("http://127.0.0.1:5173/api/jobs/1/draft/resume/print.html")
    assert out == b"%PDF-1.7 from-electron"
    # Trailing slash normalized; posts the print URL as JSON.
    assert captured["url"] == "http://127.0.0.1:9999/print"
    assert captured["json"] == {
        "url": "http://127.0.0.1:5173/api/jobs/1/draft/resume/print.html"
    }


def test_service_error_raises_unavailable(monkeypatch):
    monkeypatch.setenv(pdf.PDF_SERVICE_ENV, "http://127.0.0.1:9999")

    def _boom(url, json, timeout):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx, "post", _boom)
    with pytest.raises(pdf.PdfRendererUnavailable):
        pdf.render_to_pdf("http://x/print.html")


def test_falls_back_to_playwright_when_no_service(monkeypatch):
    monkeypatch.delenv(pdf.PDF_SERVICE_ENV, raising=False)
    called = {}

    def _fake(url):
        called["url"] = url
        return b"%PDF"

    monkeypatch.setattr(pdf, "_render_via_playwright", _fake)
    out = pdf.render_to_pdf("http://x/print.html")
    assert out == b"%PDF"
    assert called["url"] == "http://x/print.html"


# ---- sidecar entry --------------------------------------------------------


def test_entry_threads_port_to_uvicorn_without_reload(monkeypatch):
    import uvicorn

    captured = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: captured.update(app=app, **kw))
    monkeypatch.setenv("JOB_APPLIER_API_PORT", "54321")

    _load_entry().main()

    assert captured["app"] == "job_applier.api.app:app"
    assert captured["port"] == 54321
    assert captured["reload"] is False
    assert captured["host"] == "127.0.0.1"
