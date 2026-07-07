"""Print-to-PDF driver — one swappable seam, two transports.

``render_to_pdf(url)`` loads a print-HTML URL in a browser engine and returns the
printed PDF bytes. Transport is chosen by environment:

- **Packaged Electron app** (``JOB_APPLIER_PDF_SERVICE`` set): POST the URL to the
  Electron main process's print service, which prints it with
  ``webContents.printToPDF``. No Playwright in the bundle.
- **Dev / standalone CLI** (default): Playwright's bundled Chromium, imported
  lazily so the app runs (and non-PDF features work) even when it is not installed.

Both hit the same ``.../draft/{kind}/print.html`` endpoint and CSS, so output stays
consistent across dev and the packaged app.
"""

from __future__ import annotations

import os

# Env var Electron sets to the base URL of its in-process print service.
PDF_SERVICE_ENV = "JOB_APPLIER_PDF_SERVICE"


class PdfRendererUnavailable(RuntimeError):
    """Raised when no browser engine is available to print PDFs."""


def render_to_pdf(url: str) -> bytes:
    """Render the page at ``url`` to PDF bytes via the active transport."""
    service = os.environ.get(PDF_SERVICE_ENV)
    if service:
        return _render_via_service(service, url)
    return _render_via_playwright(url)


def _render_via_service(service: str, url: str) -> bytes:
    """Print through Electron's ``webContents.printToPDF`` over loopback HTTP."""
    import httpx

    try:
        resp = httpx.post(
            f"{service.rstrip('/')}/print",
            json={"url": url},
            timeout=60.0,
        )
        resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001 - surface a clear error to the caller
        raise PdfRendererUnavailable(
            f"Electron print service failed ({exc})."
        ) from exc
    return resp.content


def _render_via_playwright(url: str) -> bytes:
    """Render via headless Chromium. ``print_background`` keeps colored rules;
    ``prefer_css_page_size`` lets the draft CSS ``@page`` rules drive size/margins."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - exercised only without playwright
        raise PdfRendererUnavailable(
            "Playwright is not installed. Install the PDF renderer with "
            "`uv sync --extra pdf` (or `pip install playwright`) and then "
            "`uv run playwright install chromium`."
        ) from exc

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                page = browser.new_page()
                page.goto(url, wait_until="networkidle")
                return page.pdf(print_background=True, prefer_css_page_size=True)
            finally:
                browser.close()
    except PdfRendererUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001 - surface a clear install hint
        # Most commonly the Chromium binary was never downloaded.
        raise PdfRendererUnavailable(
            f"Headless Chromium failed to render PDF ({exc}). If Chromium is "
            "missing, run `uv run playwright install chromium`."
        ) from exc
