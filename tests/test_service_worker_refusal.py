"""A page that asks for a service worker is refused as a browser refuses it
(docs/09 P135-3).

WebShot blocks service workers: a worker's own requests do not cross
`context.route`, so the request policy would not see them (docs/09 P7-6).
Playwright's block replaced `register` with a function whose promise resolved
to nothing, which no browser does. An embedded chart read `.active` off it,
threw, and never drew itself: a news page printed two loading spinners where
its charts were, and the frame warning said their text was in the PDF. A
refusal is a rejected promise, which pages are written to handle.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from playwright.async_api import async_playwright
from pypdf import PdfReader

from webshot.capture.session import launch_context
from webshot.cli import main
from webshot.config import CaptureOptions

FIXTURE = Path(__file__).parent / "fixtures" / "service_worker_chart_page.html"


@pytest.mark.browser
def test_a_chart_that_asks_for_a_service_worker_draws_itself(tmp_path: Path) -> None:
    pdf = tmp_path / "capture.pdf"
    assert main([str(FIXTURE), "--output", str(pdf), "--no-ocr"]) == 0
    text = " ".join(
        " ".join(
            page.extract_text() or "" for page in PdfReader(str(pdf)).pages
        ).split()
    )
    assert "Chart: twelve boats returned before the squall." in text
    assert "Loading the chart" not in text
    bundle = (tmp_path / "capture.ai" / "content.txt").read_text(encoding="utf-8")
    assert "twelve boats returned" in bundle


@pytest.mark.browser
def test_registration_is_refused_and_no_worker_starts(tmp_path: Path) -> None:
    """Rejected, as a browser that will not start the worker rejects it, and
    still refused: no worker in the context."""
    options = CaptureOptions(source=str(FIXTURE), output=tmp_path / "unused.pdf")

    async def run() -> tuple[str, int]:
        async with async_playwright() as playwright:
            context, browser = await launch_context(playwright, options)
            try:
                page = await context.new_page()
                await page.goto(FIXTURE.as_uri())
                outcome = await page.evaluate(
                    """() => navigator.serviceWorker.register('chart-worker.js').then(
                        registration => `resolved ${typeof registration}`,
                        error => `rejected ${error.name}`)"""
                )
                return str(outcome), len(context.service_workers)
            finally:
                await context.close()
                if browser:
                    await browser.close()

    assert asyncio.run(run()) == ("rejected SecurityError", 0)
