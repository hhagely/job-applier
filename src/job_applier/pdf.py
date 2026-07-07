"""Print-to-PDF driver (dev / standalone-CLI path).

Single swappable seam: ``render_to_pdf(url)`` loads a print-HTML URL in a
headless browser engine and returns the printed PDF bytes. Here it uses
Playwright's bundled Chromium; in the packaged app (Phase 6) Electron's
``webContents.printToPDF`` replaces this driver — both hit the same
``.../draft/{kind}/print.html`` endpoint and the same CSS, so output stays
consistent. Playwright is an optional/dev dependency and is imported lazily so
the app runs (and non-PDF features work) even when it is not installed.
"""

from __future__ import annotations


class PdfRendererUnavailable(RuntimeError):
    """Raised when no headless browser engine is available to print PDFs."""


def render_to_pdf(url: str) -> bytes:
    """Render the page at ``url`` to PDF bytes via headless Chromium.

    ``print_background=True`` keeps colored rules/backgrounds; ``prefer_css_page_size``
    lets the ``@page`` rules in the draft CSS drive page size and margins (matching
    the pre-WebView weasyprint output).
    """
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
