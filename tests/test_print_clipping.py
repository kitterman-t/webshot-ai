"""Text the PDF loses at the page edge or in a scrolling box (docs/09 P16-1, P16-4, P16-8).

A print stylesheet can lay a column out past the paper's edge. Chromium clips
it, so the render and its text layer lose words the bundle — read from the DOM
— still has. Nothing said so. These tests hold the check that now does, from
both sides: it must fire on the layout that loses text, and it must stay quiet
on the layouts that only look like it — a page Chromium shrinks to fit, a
carousel's hidden slides, a closed off-canvas menu. A check that cries wolf on
those would record a loss the PDF does not have.

The width is the other thing worth pinning. It is Chromium's, read back from a
probe print, not the paper's: a page's own `@page` margin and Chromium's
shrink-to-fit both move it, and a check at the paper's width would measure the
wrong page in both cases.

A box that scrolls on screen is the second way (P16-8): a wide table's
wrapper, a capped log. The PDF shows what was scrolled into view and nothing
else. It is its own tally with its own warning, because the fix is the box,
not the page, and a box with room to spare or a carousel that hides its
slides from the screen too must not count.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from playwright.async_api import async_playwright
from pypdf import PdfReader

from webshot.cli import main
from webshot.config import CaptureOptions
from webshot.render.clipping import (
    PrintClipping,
    clipping_warnings,
    find_clipped_text,
)

FIXTURE = Path(__file__).parent / "fixtures" / "print_overflow.html"
SCROLL_FIXTURE = Path(__file__).parent / "fixtures" / "scroll_containers.html"

#: Long enough to fill several lines in any font at any width used here.
PARAGRAPH = (
    "Tutors meet students in person and on video, and every session starts "
    "from the draft the student brings rather than from a fixed lesson plan. "
) * 3

#: Letter at 0.6in margins and scale 0.9: 526pt of content is 779 CSS px.
PAPER_WIDTH = 779


async def _measure(html: str) -> tuple[PrintClipping, dict[str, int] | None]:
    options = CaptureOptions(source="fixture", output=Path("unused.pdf"))
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        try:
            page = await browser.new_page(viewport={"width": 1440, "height": 1000})
            if html.startswith("file:"):
                await page.goto(html)
            else:
                await page.set_content(html)
            # As the pipeline leaves it: print media emulated before the render.
            await page.emulate_media(media="print")
            clipping = await find_clipped_text(page, options)
            return clipping, page.viewport_size
        finally:
            await browser.close()


def _page(print_css: str, body: str | None = None) -> str:
    body = body or (
        '<div class="wrap"><div class="col"><h2>Meet a tutor</h2>'
        f"<p>{PARAGRAPH}</p><p>{PARAGRAPH}</p></div></div>"
    )
    return (
        "<!doctype html><html><head><style>"
        "body { margin: 0; font: 16px/1.5 Helvetica, Arial, sans-serif; }"
        f"@media print {{ {print_css} }}"
        f"</style></head><body><p>{PARAGRAPH}</p>{body}</body></html>"
    )


# --------------------------------------------------------------------------- #
# The warning
# --------------------------------------------------------------------------- #


def test_nothing_cut_is_no_warning() -> None:
    assert clipping_warnings(PrintClipping(width=PAPER_WIDTH)) == []


def test_the_warning_names_the_count_and_where_to_look() -> None:
    [message] = clipping_warnings(
        PrintClipping(width=954, elements=24, sample="section#content-column")
    )
    assert "24 element(s)" in message
    assert "section#content-column" in message
    assert "--css" in message


def test_a_scrolling_box_gets_its_own_warning() -> None:
    """The two losses have different fixes, so one warning each."""
    edge, scrolled = clipping_warnings(
        PrintClipping(
            width=PAPER_WIDTH,
            elements=2,
            sample="div.col",
            scrolled=5,
            scrolled_sample="div.table-wrap",
        )
    )
    assert "page edge" in edge and "div.col" in edge
    assert "5 element(s)" in scrolled and "div.table-wrap" in scrolled
    assert "cannot scroll" in scrolled


def test_a_check_that_could_not_measure_says_so() -> None:
    """Absence is not success: an unmeasured page is not a page with nothing cut."""
    [message] = clipping_warnings(PrintClipping(width=None))
    assert "did not check" in message
    assert "scrolling box" in message


# --------------------------------------------------------------------------- #
# The measurement, against a real print layout
# --------------------------------------------------------------------------- #


@pytest.mark.browser
def test_the_fixture_column_is_cut_and_named() -> None:
    """P16-1's layout: a fixed column pushed right inside a centred container.

    The width is past the paper's because Chromium laid the too-wide page out
    again — and the column is cut regardless, because the centred container
    moved right with the page. Three justified paragraphs and the right-set
    sentinel are cut; the short heading is not, and is not counted.
    """
    clipping, _ = asyncio.run(_measure(FIXTURE.as_uri()))
    assert clipping.width is not None and clipping.width > PAPER_WIDTH
    assert clipping.elements == 4
    assert clipping.sample == "section#content-column.column"
    assert clipping.scrolled == 0


@pytest.mark.browser
def test_the_viewport_is_put_back() -> None:
    """The journey walker keeps using the page after its capture."""
    _, viewport = asyncio.run(_measure(FIXTURE.as_uri()))
    assert viewport == {"width": 1440, "height": 1000}


@pytest.mark.browser
def test_a_page_chromium_shrinks_to_fit_is_not_a_finding() -> None:
    """A fixed 1000px column is wider than the paper and still prints whole."""
    clipping, _ = asyncio.run(_measure(_page(".col { width: 1000px; }")))
    assert clipping.width is not None and clipping.width >= 999
    assert clipping.elements == 0


@pytest.mark.browser
def test_past_the_shrink_limit_is_a_finding() -> None:
    """Chromium widens a page by half again at most, then clips."""
    clipping, _ = asyncio.run(_measure(_page(".col { width: 1400px; }")))
    assert clipping.width is not None and clipping.width < 1400
    assert clipping.elements == 2
    assert clipping.sample == "div.wrap > div.col"


@pytest.mark.browser
def test_a_page_that_hides_its_overflow_is_cut_at_the_paper_edge() -> None:
    """`overflow-x: hidden` on the root leaves Chromium nothing to shrink."""
    clipping, _ = asyncio.run(
        _measure(_page("html, body { overflow-x: hidden; } .col { width: 1000px; }"))
    )
    assert clipping.width == PAPER_WIDTH
    assert clipping.elements == 2


@pytest.mark.browser
def test_the_page_s_own_page_margin_sets_the_width() -> None:
    """`@page { margin }` widens the layout even without --prefer-css-page-size.

    At the paper's 779px this column would be a finding; at the 864px Chromium
    actually lays it out at, it fits.
    """
    clipping, _ = asyncio.run(
        _measure(_page("@page { margin: 0.5cm; } .col { width: 860px; }"))
    )
    assert clipping.width is not None and clipping.width > 860
    assert clipping.elements == 0


@pytest.mark.browser
def test_the_left_edge_cuts_too() -> None:
    clipping, _ = asyncio.run(
        _measure(_page(".col { position: relative; left: -100px; }"))
    )
    # The heading starts off the page as well, so it is cut with the paragraphs.
    assert clipping.elements == 3


@pytest.mark.browser
def test_a_carousel_s_other_slides_are_not_a_finding() -> None:
    """They are clipped by the carousel, at any paper width, on purpose."""
    slides = "".join(
        f'<div style="width:25%"><p>Slide {n}. {PARAGRAPH}</p></div>' for n in range(4)
    )
    carousel = (
        '<div style="overflow:hidden;width:100%">'
        f'<div style="display:flex;width:400%">{slides}</div></div>'
    )
    clipping, _ = asyncio.run(_measure(_page("", body=carousel)))
    assert clipping.elements == 0
    assert clipping.scrolled == 0


@pytest.mark.browser
def test_a_carousel_slide_starting_inside_the_page_is_not_a_finding() -> None:
    """Padding puts the second slide's first words inside the page edge.

    Its line then crosses the edge, which is the page-edge rule's signal, but it
    lies wholly outside the carousel's box, which hides it on screen as well.
    The first version of this check counted it (docs/09 P16-8).
    """
    slides = "".join(
        f'<div style="width:33.333%"><p>Slide {n}. {PARAGRAPH}</p></div>'
        for n in range(3)
    )
    carousel = (
        '<div style="padding:0 40px"><div style="overflow:hidden">'
        f'<div style="display:flex;width:300%">{slides}</div></div></div>'
    )
    clipping, _ = asyncio.run(_measure(_page("", body=carousel)))
    assert clipping.elements == 0
    assert clipping.scrolled == 0


@pytest.mark.browser
def test_a_closed_off_canvas_menu_is_not_a_finding() -> None:
    drawer = (
        '<div style="position:fixed;top:0;left:100%;width:300px">'
        "<p>Home · Services · Hours · Contact</p></div>"
        '<span style="position:absolute;left:-10000px;width:1px;height:1px;'
        'overflow:hidden">Skip to content</span>'
    )
    clipping, _ = asyncio.run(_measure(_page("", body=drawer)))
    assert clipping.elements == 0


@pytest.mark.browser
def test_a_probe_that_does_not_print_is_an_unchecked_page() -> None:
    """A print stylesheet that prints nothing leaves no width to read back."""
    clipping, _ = asyncio.run(_measure(_page("html { display: none; }")))
    assert clipping.width is None
    assert clipping_warnings(clipping) != []


#: A row of no-wrap cells no page is wide enough for.
WIDE_ROW = "".join(f"<td>Column {n} of thirty</td>" for n in range(30))


@pytest.mark.browser
def test_a_scroll_wrapper_s_hidden_columns_are_counted() -> None:
    """The responsive-table wrapper: the PDF prints the columns in view.

    Counted as scrolled and not at the page edge, although the table runs past
    both: the wrapper hid those columns first, and it is the box to fix.
    """
    wrapper = (
        '<div class="table-wrap" style="overflow-x:auto">'
        f'<table><tr style="white-space:nowrap">{WIDE_ROW}</tr></table></div>'
    )
    clipping, _ = asyncio.run(_measure(_page("", body=wrapper)))
    assert clipping.elements == 0
    assert clipping.scrolled >= 20
    assert clipping.scrolled_sample == "div.table-wrap"


@pytest.mark.browser
def test_a_capped_log_s_hidden_rows_are_counted() -> None:
    rows = "".join(f"<p>Entry {n}: the desk reopened.</p>" for n in range(30))
    log = f'<div class="log" style="max-height:6em;overflow-y:auto">{rows}</div>'
    clipping, _ = asyncio.run(_measure(_page("", body=log)))
    assert clipping.scrolled >= 20
    assert clipping.scrolled_sample == "div.log"


@pytest.mark.browser
def test_a_scrolling_box_with_room_to_spare_is_not_a_finding() -> None:
    """`overflow: auto` alone hides nothing; only a box that overflows does."""
    roomy = (
        '<div style="max-height:40em;overflow:auto">'
        f"<p>{PARAGRAPH}</p></div>"
        '<div style="overflow-x:auto"><table><tr><td>Short</td></tr></table></div>'
    )
    clipping, _ = asyncio.run(_measure(_page("", body=roomy)))
    assert clipping.scrolled == 0


@pytest.mark.browser
def test_the_scroll_fixture_counts_the_boxes_and_nothing_else() -> None:
    clipping, _ = asyncio.run(_measure(SCROLL_FIXTURE.as_uri()))
    assert clipping.elements == 0
    # At least the header cell and the log row the end-to-end test looks for;
    # how many more depends on the font's widths, so it is not pinned.
    assert clipping.scrolled >= 2
    assert clipping.scrolled_sample == "div.table-wrap"


# --------------------------------------------------------------------------- #
# End to end: where the warning lands, and that the fixture really loses text
# --------------------------------------------------------------------------- #


def _capture(fixture: Path, directory: Path) -> Path:
    output = directory / "capture.pdf"
    report = directory / "report.json"
    code = main([str(fixture), "--output", str(output), "--report", str(report)])
    # Not fatal: the PDF is still the page, less what was lost, and the
    # warning is what says so (docs/04-spec.md §5).
    assert code == 0, f"capture failed with exit {code}"
    return output


@pytest.fixture(scope="module")
def overflow_capture(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """One capture of each fixture, reused: each one drives a real browser."""
    return _capture(FIXTURE, tmp_path_factory.mktemp("print-overflow"))


@pytest.fixture(scope="module")
def scroll_capture(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return _capture(SCROLL_FIXTURE, tmp_path_factory.mktemp("scroll-containers"))


def _records(capture: Path) -> list[list[str]]:
    """The warnings of the manifest, the QA report and the embedded `capture.json`."""
    manifest = json.loads(
        (capture.with_suffix(".ai") / "manifest.json").read_text("utf-8")
    )
    report = json.loads((capture.parent / "report.json").read_text("utf-8"))
    embedded = json.loads(
        PdfReader(str(capture)).attachments["capture.json"][0].decode()
    )
    return [manifest["warnings"], report["warnings"], embedded["warnings"]]


def _texts(capture: Path) -> tuple[str, str]:
    """The bundle's Markdown, from the DOM, and the PDF's text layer, from the render."""
    markdown = (capture.with_suffix(".ai") / "content.md").read_text("utf-8")
    rendered = "".join(page.extract_text() for page in PdfReader(str(capture)).pages)
    return markdown, rendered


def _clipping_warnings(warnings: list[str]) -> list[str]:
    return [
        w
        for w in warnings
        if w.startswith(("Text runs off the printed page", "Text is hidden in a"))
    ]


@pytest.mark.browser
def test_the_warning_reaches_every_record(overflow_capture: Path) -> None:
    """Manifest, report and the `capture.json` the PDF carries all say it once."""
    manifest, report, embedded = _records(overflow_capture)
    found = _clipping_warnings(manifest)
    assert len(found) == 1, manifest
    assert found[0].startswith("Text runs off the printed page")
    assert "4 element(s)" in found[0]
    assert _clipping_warnings(report) == found
    assert _clipping_warnings(embedded) == found


@pytest.mark.browser
def test_the_fixture_loses_text_the_bundle_keeps(overflow_capture: Path) -> None:
    """The defect the warning reports is real, not a measurement artefact.

    If this ever fails because the sentinel reached the PDF, the layout stopped
    clipping — a Chromium change, or a correction added to WebShot — and the
    warning above has become a false one.
    """
    markdown, rendered = _texts(overflow_capture)
    assert "PRINTBASELINEALPHA" in markdown and "PRINTBASELINEALPHA" in rendered
    assert "OFFPAGEOMEGA" in markdown
    assert "OFFPAGEOMEGA" not in rendered


@pytest.mark.browser
def test_a_scrolling_box_warning_reaches_every_record(scroll_capture: Path) -> None:
    """One scrolling-box warning, and no page-edge one: nothing ran off the page."""
    manifest, report, embedded = _records(scroll_capture)
    found = _clipping_warnings(manifest)
    assert len(found) == 1, manifest
    assert found[0].startswith("Text is hidden in a scrolling box")
    assert "div.table-wrap" in found[0]
    assert _clipping_warnings(report) == found
    assert _clipping_warnings(embedded) == found


@pytest.mark.browser
def test_the_scroll_fixture_loses_text_the_bundle_keeps(scroll_capture: Path) -> None:
    """The table's hidden columns are lost; the log's rows are not (P16-10).

    The log is capped by its own `max-height`, so `release_clamped_text` lets it
    take its full height and every row prints. The table wrapper scrolls
    sideways, which stays warn-only, so its hidden columns are still missing,
    and that is the warning above. The carousel's hidden slide is missing too,
    and is not a finding, because the screen does not show it either. The unit
    test above holds that by count.
    """
    markdown, rendered = _texts(scroll_capture)
    assert "SCROLLBASELINEALPHA" in markdown and "SCROLLBASELINEALPHA" in rendered
    assert "SCROLLEDROWSIGMA" in markdown and "SCROLLEDROWSIGMA" in rendered
    for hidden in ("SCROLLEDCOLUMNOMEGA", "CAROUSELHIDDENTAU"):
        assert hidden in markdown, hidden
        assert hidden not in rendered, hidden
