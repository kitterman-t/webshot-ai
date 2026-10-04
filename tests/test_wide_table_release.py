"""A table in a sideways scroller, let wrap for print where it then fits (docs/09 P16-11).

On paper a scrolling wrapper prints only the columns scrolled into view: three
of six in the measured case (P16-8). Letting the cells wrap fits many tables
on the page and prints every column. It does not fit all of them. A table
released anyway runs past the page edge, and Chromium then shrinks the whole
page to fit it.

So the release is tried at the width the page prints at and kept only where
the table then fits. These tests hold both sides: the table that fits is
released and prints whole, the one that does not keeps scrolling and is
reported, and nothing that is not a table in a sideways scroller is touched.
"""

from __future__ import annotations

import asyncio
import io
import json
from pathlib import Path

import pytest
from playwright.async_api import async_playwright
from pypdf import PdfReader

from webshot.capture.prepare import (
    BASE_PRINT_CSS,
    has_scrolling_tables,
    release_clamped_text,
    release_wide_tables,
)
from webshot.cli import main
from webshot.config import CaptureOptions
from webshot.render.clipping import find_clipped_text, printed_width
from webshot.render.pdf import print_geometry

FIXTURE = Path(__file__).parent / "fixtures" / "wide_tables.html"


async def _release(html: str) -> dict[str, object]:
    """Prepare a page the way the pipeline does, print it, and report."""
    options = CaptureOptions(source="fixture", output=Path("unused.pdf"))
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        try:
            page = await browser.new_page(viewport={"width": 1440, "height": 1000})
            if html.startswith("file:"):
                await page.goto(html)
            else:
                await page.set_content(html)
            await release_clamped_text(page)
            await page.add_style_tag(content=BASE_PRINT_CSS)
            await page.emulate_media(media="print")
            found = await has_scrolling_tables(page)
            width = await printed_width(page, options) if found else None
            released = await release_wide_tables(page, width) if width else 0
            marks = await page.evaluate(
                """() => [...document.querySelectorAll('[data-webshot-wrapped]')]
                    .map(e => e.id)"""
            )
            vertical = await page.evaluate(
                """() => [...document.querySelectorAll('[data-webshot-clamped]')]
                    .map(e => `${e.id}:${e.dataset.webshotClamped}`)"""
            )
            viewport = page.viewport_size
            pdf = await page.pdf(**print_geometry(options))
            text = "".join(p.extract_text() for p in PdfReader(io.BytesIO(pdf)).pages)
            clipping = await find_clipped_text(page, options)
            return {
                "found": found,
                "released": released,
                "marks": marks,
                "vertical": vertical,
                "viewport": viewport,
                "text": text,
                "clipping": clipping,
            }
        finally:
            await browser.close()


def _page(body: str) -> str:
    return (
        "<!doctype html><html><head><style>"
        "body { max-width: 46rem; margin: 0 auto; padding: 2rem 1.5rem;"
        " font: 16px/1.5 Helvetica, Arial, sans-serif; }"
        "td, th { padding: 0 12px; white-space: nowrap; }"
        f"</style></head><body>{body}</body></html>"
    )


@pytest.mark.browser
def test_the_table_that_fits_wrapped_is_released_and_the_other_is_not() -> None:
    result = asyncio.run(_release(FIXTURE.as_uri()))
    assert result["found"] is True
    assert result["marks"] == ["fits-wrapped"]
    text = str(result["text"])
    assert "WRAPPEDCOLUMNOMEGA" in text
    assert "UNWRAPPABLEOMEGA" not in text
    clipping = result["clipping"]
    # The table that could not be released is still reported, and it is the
    # only scrolling box left.
    assert clipping.scrolled > 0
    assert clipping.scrolled_sample == "div#too-wide.table-wrap"
    assert clipping.elements == 0


@pytest.mark.browser
def test_the_viewport_is_put_back() -> None:
    """The bundle is extracted from this page next, at the capture's viewport."""
    result = asyncio.run(_release(FIXTURE.as_uri()))
    assert result["viewport"] == {"width": 1440, "height": 1000}


@pytest.mark.browser
def test_a_page_without_a_scrolling_table_pays_for_no_probe() -> None:
    """A code block or an image in a scroller is no candidate: nothing wraps it."""
    body = (
        '<pre style="overflow-x:auto">' + "x" * 400 + "</pre>"
        '<div style="overflow-x:auto"><div style="width:2000px">A wide block.</div></div>'
    )
    result = asyncio.run(_release(_page(body)))
    assert result["found"] is False
    assert result["released"] == 0


@pytest.mark.browser
def test_a_table_that_already_fits_is_left_alone() -> None:
    body = '<div id="narrow" style="overflow-x:auto"><table><tr><td>Short</td></tr></table></div>'
    result = asyncio.run(_release(_page(body)))
    assert result["found"] is True
    assert result["marks"] == []


@pytest.mark.browser
def test_a_wrapper_released_vertically_keeps_that_release() -> None:
    """Both axes, both marks: the sideways one is an attribute of its own."""
    cells = "".join(f"<td>open {n} to six on weekdays</td>" for n in range(6))
    rows = "".join(f"<tr>{cells}</tr>" for _ in range(20))
    body = f'<div id="both" style="max-height:8em;overflow:auto"><table>{rows}</table></div>'
    result = asyncio.run(_release(_page(body)))
    assert result["marks"] == ["both"]
    assert result["vertical"] == ["both:scroll"]


@pytest.fixture(scope="module")
def wide_capture(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """One capture of the fixture through the command line, reused."""
    directory = tmp_path_factory.mktemp("wide-tables")
    output = directory / "capture.pdf"
    report = directory / "report.json"
    code = main([str(FIXTURE), "--output", str(output), "--report", str(report)])
    assert code == 0, f"capture failed with exit {code}"
    return output


@pytest.mark.browser
def test_the_capture_prints_the_released_table_and_reports_the_other(
    wide_capture: Path,
) -> None:
    manifest = json.loads(
        (wide_capture.with_suffix(".ai") / "manifest.json").read_text("utf-8")
    )
    found = [w for w in manifest["warnings"] if w.startswith("Text is hidden in a")]
    assert len(found) == 1, manifest["warnings"]
    assert "div#too-wide.table-wrap" in found[0]
    assert not [
        w
        for w in manifest["warnings"]
        if w.startswith("Text runs off the printed page")
    ]
    markdown = (wide_capture.with_suffix(".ai") / "content.md").read_text("utf-8")
    rendered = "".join(p.extract_text() for p in PdfReader(str(wide_capture)).pages)
    assert "WIDEBASELINEALPHA" in rendered
    assert "WRAPPEDCOLUMNOMEGA" in markdown and "WRAPPEDCOLUMNOMEGA" in rendered
    assert "UNWRAPPABLEOMEGA" in markdown and "UNWRAPPABLEOMEGA" not in rendered
