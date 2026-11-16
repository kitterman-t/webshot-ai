"""A box wider than the printed page, capped at its container's width in clean mode (docs/09 P123-3).

A code host's repository page held its README in a flex column that could not
shrink below its widest table, 1,078px in a 780px page. The PDF cut the
README's lines at the right edge and lost its last two sections, headings and
all, and the one warning counted elements with lines past the edge.
`--mode faithful`, `--media screen` and `--scale 0.7` were no better.

Clean mode now caps such a box at its container's width, before the bundle,
where that cuts fewer lines at the edge and leaves no capped box with content
still running past its own edge. Faithful mode prints the page as it is, and
the headings it loses are named (P123-5).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from playwright.async_api import async_playwright
from pypdf import PdfReader

from webshot.capture.prepare import BASE_PRINT_CSS, adopt_stylesheet
from webshot.cli import main
from webshot.config import CaptureOptions
from webshot.render.clipping import REFLOWED_ATTRIBUTE, WideBoxes, release_wide_boxes

FIXTURE = Path(__file__).parent / "fixtures" / "wide_flex_column_page.html"


def _capture(tmp_path: Path, *args: str) -> tuple[list[str], str]:
    pdf = tmp_path / "capture.pdf"
    assert main([str(FIXTURE), "--output", str(pdf), "--no-ocr", *args]) == 0
    manifest = json.loads(
        (pdf.with_suffix(".ai") / "manifest.json").read_text(encoding="utf-8")
    )
    text = " ".join(
        " ".join(
            page.extract_text() or "" for page in PdfReader(str(pdf)).pages
        ).split()
    )
    return list(manifest["warnings"]), text


@pytest.mark.browser
def test_clean_mode_fits_the_record_to_the_page(tmp_path: Path) -> None:
    warnings, text = _capture(tmp_path, "--mode", "clean")
    assert warnings == []
    assert "Further surveys and archives" in text
    assert "can be read there on any weekday morning." in text
    # A cell's line breaks are read back as spaces, or as nothing.
    squashed = text.replace(" ", "")
    assert "shallowerby14centimetresafterthewinterstorms" in squashed


@pytest.mark.browser
def test_faithful_mode_prints_it_cut_and_names_the_heading_it_lost(
    tmp_path: Path,
) -> None:
    warnings, text = _capture(tmp_path, "--mode", "faithful")
    assert "Further surveys and archives" not in text
    heading, clipped = warnings
    assert heading == (
        "1 heading the page showed is in the bundle but not in the PDF's text: "
        '"Further surveys and archives". The printed layout lost it, and with it '
        "likely the section under it: text cut off at the page edge or inside a "
        "box that does not grow for print goes this way."
    )
    assert clipped.startswith("Text runs off the printed page: ")
    assert "(in div.layout > div.record)" in clipped


def _options() -> CaptureOptions:
    return CaptureOptions(source="fixture", output=Path("unused.pdf"))


async def _release(html: str) -> tuple[WideBoxes, list[str], dict[str, int] | None]:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        try:
            page = await browser.new_page(viewport={"width": 1440, "height": 900})
            await page.set_content(html)
            await adopt_stylesheet(page, BASE_PRINT_CSS)
            await page.emulate_media(media="print")
            found = await release_wide_boxes(page, _options())
            marked: Any = await page.evaluate(
                f"() => [...document.querySelectorAll('[{REFLOWED_ATTRIBUTE}]')]"
                ".map(element => element.className)"
            )
            return found, list(marked), page.viewport_size
        finally:
            await browser.close()


#: A flex item held open by a table whose cell does not wrap, which no cap can
#: narrow: its content would run past the capped item and print over what is
#: beside it.
FIXED_WIDTH = """<!doctype html><html><body style="margin: 0">
  <div style="overflow: hidden"><div style="display: flex">
    <div class="record" style="flex: 1 1 auto">
      <p class="lead">A paragraph that wraps once the record is narrower than
      the page, and runs past the page edge while the record is wide, as every
      line of it does here, from the first to the last, whatever font draws
      it, since each is longer than the page is wide.</p>
      <table><tr><td style="white-space: nowrap">A cell that does not wrap, and
      is wider than the page by itself: north quay, south quay, east quay, west
      quay, and the old ferry slip beside the lighthouse.</td></tr></table>
    </div>
  </div></div>
</body></html>"""


@pytest.mark.browser
def test_a_cap_that_would_spill_the_box_s_content_is_taken_off() -> None:
    found, marked, viewport = asyncio.run(_release(FIXED_WIDTH))
    assert found.released == 0
    assert found.width is not None  # the probe was printed: lines were cut
    assert marked == []
    assert viewport == {"width": 1440, "height": 900}


@pytest.mark.browser
def test_a_page_that_cuts_nothing_pays_for_no_probe_print() -> None:
    """A print before the render runs the page's own print handlers (P97-4)."""
    found, marked, viewport = asyncio.run(
        _release("<!doctype html><p>A short page that fits.</p>")
    )
    assert found == WideBoxes()
    assert marked == []
    assert viewport == {"width": 1440, "height": 900}


@pytest.mark.browser
def test_the_record_is_capped_and_the_window_put_back() -> None:
    found, marked, viewport = asyncio.run(_release(FIXTURE.read_text(encoding="utf-8")))
    assert found.released == len(marked) > 0
    assert "record" in marked
    assert viewport == {"width": 1440, "height": 900}
