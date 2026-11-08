"""A page's own `@page` margin and the running header and footer (docs/09 P115).

Chromium honours a page's `@page` margin over the margin `page.pdf` is given,
and the running header and footer print in that margin. A catalogue page that
loaded pdf.js's `viewer.css` carried `@page { margin: 0 }`: its fixed bar
printed over the header's title and URL, and the text layer interleaved the two
(P113-5). With the header and footer on, WebShot now hands the margins back to
`--margin`.

These tests hold that from both sides. The margins hold the header and footer
and none of the page, in either mode, for each way a page can set its margin.
A page with no `@page` rule prints exactly as it did before, which is what keeps
every golden where it was. Under `--no-header-footer` the page's own margin is
left in force. And the one case the rule does not reach, a page's own
`!important` margin, is pinned too, so the spec's claim about it is measured
rather than remembered.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pypdfium2 as pdfium
import pytest
from playwright.async_api import async_playwright

from webshot.capture.prepare import keep_header_footer_margins
from webshot.cli import main
from webshot.config import CaptureOptions
from webshot.render.pdf import header_footer_options, print_geometry
from webshot.render.pdfium_bridge import read_ink

FIXTURE = Path(__file__).parent / "fixtures" / "page_margin_zero.html"

#: The fixture's own words: the fixed bar's, and one on every line of the body.
BAR_WORD = "Catalogue"
BODY_WORD = "Holdings"
TITLE_WORD = "Zero margin record"

#: How far into the paper's top and bottom edges a band is read, in points.
#: Inside the 0.6in (43.2pt) margin with room to spare, and deep enough to take
#: in the header's and footer's lines, which end about 25pt from the edge.
BAND_PT = 38.0

PARAGRAPHS = "".join(
    f"<p>Holdings line {n}: the record lists its call number and shelf.</p>"
    for n in range(1, 81)
)


def _bands(pdf: Path | bytes) -> list[tuple[str, str]]:
    """Each page's text in its top band and in its bottom band."""
    document = pdfium.PdfDocument(str(pdf) if isinstance(pdf, Path) else pdf)
    try:
        bands = []
        for index in range(len(document)):
            page = document[index]
            textpage = page.get_textpage()
            try:
                width, height = page.get_size()
                top = textpage.get_text_bounded(0, height - BAND_PT, width, height)
                bottom = textpage.get_text_bounded(0, 0, width, BAND_PT)
                bands.append((top, bottom))
            finally:
                textpage.close()
                page.close()
        return bands
    finally:
        document.close()


def _capture(directory: Path, *flags: str) -> Path:
    output = directory / "capture.pdf"
    code = main([str(FIXTURE), "--output", str(output), "--no-ai-bundle", *flags])
    assert code == 0, f"capture failed with exit {code}"
    return output


def _page(page_css: str) -> str:
    return (
        '<!doctype html><html><head><meta charset="utf-8"><title>Margins</title>'
        f"<style>{page_css} body {{ margin: 0; font: 15px/1.5 Helvetica, Arial, "
        "sans-serif; } main { page: record; }</style></head>"
        f'<body><main><div class="bar">Catalogue Search</div>{PARAGRAPHS}</main>'
        "</body></html>"
    )


async def _print(html: str, *, keep: bool) -> bytes:
    """Print `html` as a capture does, with or without the margin rule."""
    options = CaptureOptions(source="fixture", output=Path("unused.pdf"))
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        try:
            page = await browser.new_page()
            await page.set_content(html)
            if keep:
                await keep_header_footer_margins(page)
            await page.emulate_media(media="print")
            return await page.pdf(
                **print_geometry(options),
                print_background=True,
                **header_footer_options(
                    options, title="Margins", final_url="https://example.test/"
                ),
            )
        finally:
            await browser.close()


def _glyphs(pdf: bytes) -> list[tuple[int, str, float, float]]:
    """Every character of every page, where it was drawn."""
    return [
        (page.number, glyph.char, round(glyph.x, 2), round(glyph.y, 2))
        for page in read_ink(pdf)
        for glyph in page.glyphs
    ]


# --------------------------------------------------------------------------- #
# End to end: the fixture, as captured
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def captures(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    """One capture per case, reused: each one drives a real browser."""
    return {
        "faithful": _capture(tmp_path_factory.mktemp("faithful"), "--mode", "faithful"),
        "clean": _capture(tmp_path_factory.mktemp("clean")),
        "bare": _capture(
            tmp_path_factory.mktemp("bare"), "--mode", "faithful", "--no-header-footer"
        ),
    }


@pytest.mark.browser
@pytest.mark.parametrize("mode", ["faithful", "clean"])
def test_the_margins_hold_the_header_and_footer_and_none_of_the_page(
    captures: dict[str, Path], mode: str
) -> None:
    """P113-5's capture, mended: the bar and the body stay out of both margins.

    Faithful mode printed the bar over the header; clean mode hides the bar but
    its body still ran up under the header and down under the footer.
    """
    bands = _bands(captures[mode])
    assert len(bands) > 1, "the fixture must run to a second page"
    for number, (top, bottom) in enumerate(bands, start=1):
        # Not vacuous: the bands are where the header and footer are.
        assert TITLE_WORD in top, f"page {number}'s top band: {top!r}"
        assert "WebShot" in bottom, f"page {number}'s bottom band: {bottom!r}"
        for word in (BAR_WORD, BODY_WORD):
            assert word not in top, f"page {number}'s top band: {top!r}"
            assert word not in bottom, f"page {number}'s bottom band: {bottom!r}"


@pytest.mark.browser
def test_the_fixed_bar_still_prints_in_faithful_mode(
    captures: dict[str, Path],
) -> None:
    """Moved into the margins' inside, not lost: faithful mode keeps the bar."""
    document = pdfium.PdfDocument(str(captures["faithful"]))
    try:
        textpage = document[0].get_textpage()
        assert BAR_WORD in textpage.get_text_range()
    finally:
        document.close()


@pytest.mark.browser
def test_without_the_header_and_footer_the_page_keeps_its_own_margin(
    captures: dict[str, Path],
) -> None:
    """Nothing of WebShot's needs the room, so the page's `margin: 0` stands."""
    bands = _bands(captures["bare"])
    top, bottom = bands[0]
    assert BAR_WORD in top, top
    assert BODY_WORD in bottom, bottom
    assert TITLE_WORD not in top and "WebShot" not in bottom


# --------------------------------------------------------------------------- #
# The rule, printed directly
# --------------------------------------------------------------------------- #


@pytest.mark.browser
def test_a_page_with_no_page_rule_prints_as_it_did() -> None:
    """`revert` hands the margin back to `page.pdf`'s, to the glyph.

    Writing `--margin`'s length into the CSS instead put the text 0.75pt
    lower, which would have moved every capture and every golden (P115).
    """
    html = _page("")
    before = asyncio.run(_print(html, keep=False))
    after = asyncio.run(_print(html, keep=True))
    assert _glyphs(after) == _glyphs(before)


@pytest.mark.browser
@pytest.mark.parametrize(
    "page_css",
    [
        "@page { margin: 0; }",
        "@page { margin: 2cm; }",
        "@page { margin-top: 0; margin-bottom: 0; }",
        "@page :first { margin: 0; } @page { margin: 0.5cm; }",
        "@page :left { margin: 0; } @page :right { margin: 0; }",
        "@page record { margin: 0; }",
        "@media print { @page { margin: 0; } }",
    ],
)
def test_every_page_margin_rule_prints_as_if_the_page_had_none(page_css: str) -> None:
    """Whatever `@page` margin the page sets, the PDF has `--margin`'s."""
    reference = asyncio.run(_print(_page(""), keep=False))
    kept = asyncio.run(_print(_page(page_css), keep=True))
    assert _glyphs(kept) == _glyphs(reference)


@pytest.mark.browser
def test_a_page_s_own_important_margin_still_wins() -> None:
    """The limit the spec states, measured (P115).

    Chromium lets the earliest important `@page` declaration win, and an
    adopted sheet always comes last. If this starts failing, Chromium has
    changed that order: the rule now reaches these pages too, and the spec and
    P115 say otherwise.
    """
    page_css = "@page { margin: 0 !important; }"
    plain = asyncio.run(_print(_page(page_css), keep=False))
    kept = asyncio.run(_print(_page(page_css), keep=True))
    assert _glyphs(kept) == _glyphs(plain)
    assert _glyphs(kept) != _glyphs(asyncio.run(_print(_page(""), keep=False)))
