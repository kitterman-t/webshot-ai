"""Page text a fixed or sticky box prints over, named in a warning (docs/09 P83).

Chromium prints a box fixed to the window on every page, at the same place, so
a site's pinned header or notice bar covers lines of text on page after page,
which a reader on screen would have scrolled out from under it. A sticky box
prints once, where the window left it. Before this check nothing
said so; P79 hid the consent managers' dialogs by name and left every other
box, which this check now reports instead.

The tests hold it from both sides. It must fire on the box that covers text,
counting exactly the characters under it on each printed page. And it must
stay quiet on the boxes that only look like it: a sticky masthead printed in
its place, a badge in the gutter, a panel faded to a tint, a layer that paints
nothing but catches clicks, a line the page raises above the box, and a
consent dialog P79 already hid. The measurement itself must leave the page as
it found it, because the journey walker goes on using the page.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from playwright.async_api import async_playwright
from pypdf import PdfReader

from webshot.cli import main
from webshot.render.covering import (
    INK_ATTRIBUTE,
    LAYOUT_MOVED_SCRIPT,
    MARK_BOXES_SCRIPT,
    NO_FILTER_ID,
    RESTORE_BOXES_SCRIPT,
    CoveredText,
    Covering,
    MarkedBox,
    covered_text_warnings,
    measure,
    unread_text,
)
from webshot.render.pdfium_bridge import Glyph, Ink, PageInk

FIXTURE = Path(__file__).parent / "fixtures" / "covering_boxes_page.html"

BAR = (9, 9, 9)
STICKY = (8, 8, 8)
OTHER = (7, 7, 7)


def _text(
    order: int, x: float, y: float, chars: str, color=(0, 0, 0), alpha: float = 1.0
):
    """A text object of one line and its glyphs, 6pt apart, starting at `x`."""
    ink = Ink(order, "text", (x, y - 4, x + 6 * len(chars), y + 4), color, alpha)
    glyphs = [
        Glyph(
            index=order * 100 + offset,
            order=-1 if char == " " else order,
            char=char,
            x=x + 6 * offset + 3,
            y=y,
            baseline=y - 3,
        )
        for offset, char in enumerate(chars)
    ]
    return ink, glyphs


def _page(number: int, *objects: tuple[Ink, list[Glyph]] | Ink) -> PageInk:
    inks: list[Ink] = []
    glyphs: list[Glyph] = []
    for item in objects:
        if isinstance(item, Ink):
            inks.append(item)
        else:
            inks.append(item[0])
            glyphs.extend(item[1])
    return PageInk(number=number, inks=tuple(inks), glyphs=tuple(glyphs))


def _bar(order: int, alpha: float = 1.0, color=BAR) -> Ink:
    """The box's background: a band from y 0 to 50 across the page."""
    return Ink(order, "path", (0, 0, 600, 50), color, alpha)


BOXES = (
    MarkedBox(name="div.bar", position="fixed", code=BAR),
    MarkedBox(name="th.sticky", position="sticky", code=STICKY),
    MarkedBox(name="div.other", position="fixed", code=OTHER),
)


def test_text_drawn_before_the_box_and_under_it_is_covered() -> None:
    found = measure([_page(1, _text(0, 10, 20, "hidden words"), _bar(1))], BOXES)
    assert found == (
        Covering(
            name="div.bar",
            position="fixed",
            pages=(1,),
            printed=(1,),
            characters=11,
            lines=1,
            sample="hidden words",
        ),
    )


def test_text_the_page_raises_above_the_box_is_not() -> None:
    """Drawn after the box, so on top of it: z-order counts, not overlap."""
    assert measure([_page(1, _bar(0), _text(1, 10, 20, "raised"))], BOXES) == ()


def test_a_box_drawn_at_under_half_opacity_hides_nothing() -> None:
    assert (
        measure([_page(1, _text(0, 10, 20, "tinted"), _bar(1, alpha=0.3))], BOXES) == ()
    )


def test_text_nobody_can_read_cannot_be_covered() -> None:
    """The invisible layer WebShot prints recognized text in is drawn at 1%."""
    page = _page(1, _text(0, 10, 20, "recognized", alpha=0.01), _bar(1))
    assert measure([page], BOXES) == ()


def test_only_the_centre_of_a_character_counts() -> None:
    """A line whose glyphs' centres sit just above the band is not covered."""
    assert measure([_page(1, _text(0, 10, 52, "above"), _bar(1))], BOXES) == ()


def test_a_fixed_box_over_another_is_chrome_over_chrome_but_a_sticky_box_is_the_page() -> (
    None
):
    page = _page(
        1,
        _text(0, 10, 10, "other", color=OTHER),
        _text(1, 10, 30, "heading", color=STICKY),
        _bar(2),
    )
    (found,) = measure([page], BOXES)
    assert (found.name, found.characters, found.sample) == ("div.bar", 7, "heading")


def test_a_box_does_not_cover_its_own_text() -> None:
    assert measure([_page(1, _bar(0), _text(1, 10, 20, "own", color=BAR))], BOXES) == ()


def test_a_box_whose_colours_did_not_take_is_not_measured() -> None:
    unmarked = (
        MarkedBox(name="div.bar", position="fixed", code=BAR, unmarked="kept color"),
    )
    assert measure([_page(1, _text(0, 10, 20, "words"), _bar(1))], unmarked) == ()


def test_pages_lines_and_the_sample_are_counted_across_pages() -> None:
    pages = [
        _page(1, _text(0, 10, 40, "first line"), _text(1, 10, 20, "second"), _bar(2)),
        _page(2, _text(0, 10, 300, "clear"), _bar(1)),
        _page(3, _text(0, 10, 20, "third"), _bar(1)),
    ]
    (found,) = measure(pages, BOXES)
    assert found.pages == (1, 3)
    assert found.printed == (1, 2, 3)
    assert found.characters == len("firstline") + len("second") + len("third")
    assert found.lines == 3
    assert found.sample == "first line"


def test_the_sample_stops_at_the_first_character_the_box_leaves_uncovered() -> None:
    """A line running out from under a corner box is quoted only where covered."""
    box = Ink(1, "path", (0, 0, 40, 50), BAR, 1.0)
    (found,) = measure([_page(1, _text(0, 10, 20, "abc defgh"), box)], BOXES)
    assert found.sample == "abc d"
    assert found.characters == 4


def _covering(**changes: Any) -> Covering:
    fields: dict[str, Any] = {
        "name": "div#bar.notice",
        "position": "fixed",
        "pages": (1, 2, 3),
        "printed": (1, 2, 3),
        "characters": 120,
        "lines": 6,
        "sample": "Under the bar",
    }
    fields.update(changes)
    return Covering(**fields)


def test_the_warning_names_the_box_its_pages_and_how_much_it_covers() -> None:
    assert covered_text_warnings(
        CoveredText(boxes=2, pages=3, coverings=(_covering(),))
    ) == [
        "A fixed box, div#bar.notice, prints over 120 characters of the page's text in "
        '6 lines on all 3 pages, starting "Under the bar". The text stays in the '
        "PDF's text layer, but on the page the box is drawn over it. Pass --exclude "
        '"div#bar.notice" to capture the page without the box.'
    ]


def test_a_box_on_more_pages_than_it_covers_text_on_says_both() -> None:
    """A fixed box prints on every page; what a reader loses is where it covers."""
    (warning,) = covered_text_warnings(
        CoveredText(
            boxes=1,
            pages=26,
            coverings=(_covering(pages=(2, 4), printed=tuple(range(1, 27))),),
        )
    )
    assert warning.startswith(
        "A fixed box, div#bar.notice, prints on all 26 pages, and over 120 characters "
        "of the page's text in 6 lines on pages 2 and 4, starting"
    )


@pytest.mark.parametrize(
    ("pages", "total", "phrase"),
    [
        ((2,), 5, "on page 2"),
        ((1, 2), 2, "on both pages"),
        ((1, 2, 3, 4), 4, "on all 4 pages"),
        ((2, 3, 4), 6, "on pages 2-4"),
        ((1, 3, 4, 6), 7, "on pages 1, 3-4 and 6"),
    ],
)
def test_pages_are_named_compactly(
    pages: tuple[int, ...], total: int, phrase: str
) -> None:
    (warning,) = covered_text_warnings(
        CoveredText(
            boxes=1, pages=total, coverings=(_covering(pages=pages, printed=pages),)
        )
    )
    assert f" {phrase}, " in warning


def test_one_character_on_one_line_is_singular() -> None:
    (warning,) = covered_text_warnings(
        CoveredText(
            boxes=1,
            pages=1,
            coverings=(_covering(pages=(1,), printed=(1,), characters=1, lines=1),),
        )
    )
    assert "over 1 character of the page's text in 1 line on page 1" in warning


def test_a_check_that_could_not_measure_says_so() -> None:
    """Never an empty list where the measuring print failed (CLAUDE.md)."""
    assert covered_text_warnings(
        CoveredText(boxes=3, unmeasured="the measuring print failed: Target closed")
    ) == [
        "WebShot could not check whether a fixed or sticky box prints over the page's "
        "text (the measuring print failed: Target closed). The page has 3 fixed or "
        "sticky boxes, so the PDF may show one over its text without a warning."
    ]


def test_a_count_nothing_measured_is_not_stated() -> None:
    """When the script that finds the boxes fails, nothing counted them, so
    the warning names no count rather than "0 fixed or sticky boxes"."""
    (warning,) = covered_text_warnings(
        CoveredText(boxes=None, unmeasured="the boxes could not be marked: boom")
    )
    assert warning == (
        "WebShot could not check whether a fixed or sticky box prints over the page's "
        "text (the boxes could not be marked: boom). So the PDF may show one over its "
        "text without a warning."
    )
    assert "0 fixed" not in warning
    assert CoveredText().boxes is None


def test_a_box_that_could_not_be_marked_is_named_with_what_it_kept() -> None:
    unmarked = MarkedBox(
        "div.x", "fixed", BAR, unmarked="div.x > span kept color rgb(1, 2, 3)"
    )
    assert covered_text_warnings(CoveredText(boxes=1, unmarked=(unmarked,))) == [
        "WebShot could not mark 1 fixed or sticky box to check what page text they "
        "print over, so the PDF may show them over its text without a warning: div.x "
        "(div.x > span kept color rgb(1, 2, 3)). Chromium prints a fixed box on "
        "every page, so it is on every page of the PDF."
    ]
    # A sticky box prints once, so nothing is said of every page (P97-3).
    sticky = MarkedBox("th.s", "sticky", STICKY, unmarked="th.s kept color red")
    assert covered_text_warnings(CoveredText(boxes=2, unmarked=(unmarked, sticky))) == [
        "WebShot could not mark 2 fixed or sticky boxes to check what page text they "
        "print over, so the PDF may show them over its text without a warning: div.x "
        "(div.x > span kept color rgb(1, 2, 3)); th.s (th.s kept color red). Chromium "
        "prints a fixed box on every page, so 1 of them are on every page of the PDF."
    ]


def test_text_whose_characters_belong_to_no_object_fails_the_check() -> None:
    """Absence must not read as success: unread text is no text covered."""
    ink, glyphs = _text(0, 10, 20, "words")
    orphaned = [Glyph(g.index, -1, g.char, g.x, g.y, g.baseline) for g in glyphs]
    page = PageInk(number=1, inks=(ink, _bar(1)), glyphs=tuple(orphaned))
    assert unread_text([page]) == (
        "PDFium read 1 text object(s) in the measuring print and tied no character "
        "to any of them"
    )
    assert unread_text([_page(1, _text(0, 10, 20, "words"), _bar(1))]) is None
    # A page with no text at all has nothing to tie.
    assert unread_text([_page(1, _bar(0))]) is None


def test_no_boxes_no_warnings() -> None:
    assert covered_text_warnings(CoveredText()) == []


def test_on_a_walled_page_a_box_is_not_given_the_exclude_remedy() -> None:
    """The box may be the wall, which the site keeps the article under (P97-1)."""
    [warning] = covered_text_warnings(
        CoveredText(boxes=1, pages=2, coverings=(_covering(),)), ["Mg2 ConneXt"]
    )
    assert "Pass --exclude" not in warning
    assert warning.endswith(
        "The page showed Mg2 ConneXt's access wall, and this box may be part of it: "
        "--exclude would not bring back text the site keeps under the wall."
    )


def test_a_box_gone_since_the_render_with_no_box_left_is_named() -> None:
    """With no box left there is no count to state (P97-4)."""
    assert covered_text_warnings(CoveredText(boxes=0, unmeasured="it changed")) == [
        "WebShot could not check whether a fixed or sticky box prints over the "
        "page's text (it changed). So the PDF may show one over its text without a "
        "warning."
    ]


def _capture(tmp_path: Path, *args: str) -> list[str]:
    pdf = tmp_path / "capture.pdf"
    code = main([str(FIXTURE), "--output", str(pdf), "--no-ocr", *args])
    assert code == 0
    manifest = json.loads(
        (pdf.with_suffix(".ai") / "manifest.json").read_text(encoding="utf-8")
    )
    return list(manifest["warnings"])


#: The one box in the fixture that prints over text: a line under it on each
#: page. The line the page raises above it on page two is not counted.
NOTICE_BAR = (
    "A fixed box, div.notice-bar, prints over "
    f"{len('Underthenoticebar,lineone') + len('Underthenoticebar,linetwo')} characters "
    "of the page's text in 2 lines on both pages, starting \"Under the notice bar, "
    "line one\". The text stays in the PDF's text layer, but on the page the box is "
    'drawn over it. Pass --exclude "div.notice-bar" to capture the page without the box.'
)


@pytest.mark.browser
@pytest.mark.parametrize("mode", ["faithful", "clean"])
def test_the_box_over_text_is_named_and_the_others_are_not(
    tmp_path: Path, mode: str
) -> None:
    warnings = _capture(tmp_path, "--mode", mode)
    consent = [warning for warning in warnings if "consent manager" in warning]
    assert len(consent) == 1, warnings
    assert [warning for warning in warnings if warning not in consent] == [NOTICE_BAR]


@pytest.mark.browser
def test_excluding_the_box_named_clears_the_warning(tmp_path: Path) -> None:
    warnings = _capture(tmp_path, "--mode", "faithful", "--exclude", "div.notice-bar")
    assert [warning for warning in warnings if "consent manager" not in warning] == []


#: A fixed bar with a pseudo-element, an inline style, a faint span and an SVG;
#: a sticky host whose box is drawn in its shadow root, with a transition of
#: its own; a fixed box whose inline `!important` colour no sheet can beat;
#: and WordPress's block styles beside an empty sticky box, which grew a
#: border when its colours were written inline (docs/09 P83-4).
ROUND_TRIP = """<!doctype html><html><head><style>
  .bar { position: fixed; top: 0; left: 0; right: 0; height: 40px; background: #345;
         color: #fff !important; transition: color 2s; }
  .bar::before { content: "Notice: "; color: #f80; background: #000; }
  .host { position: sticky; top: 50px; }
  html :where([style^="border-top-color"], [style*=";border-top-color"],
    [style*="; border-top-color"]) { border-top-style: solid; }
  html :where([style^="border-bottom-color"], [style*=";border-bottom-color"],
    [style*="; border-bottom-color"]) { border-bottom-style: solid; }
  .empty { position: sticky; bottom: 0; z-index: 99999; }
  @layer page { .layered { color: rgb(4, 5, 6) !important; } }
</style></head><body>
  <div class="bar" style="border: 1px solid red">Closed
    <span style="background: rgba(10, 20, 30, 0.25)">on Friday</span>
    <svg width="10" height="10"><rect width="10" height="10" fill="rgb(0, 128, 0)"/></svg></div>
  <div class="host"></div>
  <p>The page.</p>
  <div class="stubborn" style="position: fixed; top: 300px; color: rgb(1, 2, 3) !important">Mine</div>
  <div class="layered" style="position: fixed; top: 340px">Layered</div>
  <div class="empty" style="position: sticky"></div>
  <script>
    const root = document.querySelector('.host').attachShadow({mode: 'open'});
    root.innerHTML = '<style>b { color: rgb(200, 0, 0); transition: color 2s }</style><b>Inside</b>';
  </script>
</body></html>"""

#: What the page looks like to anything that reads it: its markup, the colours
#: of everything in it, and the sheets every root has adopted.
SNAPSHOT_SCRIPT = """() => {
    const shadow = document.querySelector('.host').shadowRoot;
    const elements = [...document.querySelectorAll('*'), ...shadow.querySelectorAll('*')];
    return {
        html: document.documentElement.outerHTML + shadow.innerHTML,
        colours: elements.map(element => {
            const style = getComputedStyle(element);
            return [style.color, style.backgroundColor, getComputedStyle(element, '::before').color];
        }),
        sheets: [document.adoptedStyleSheets.length, shadow.adoptedStyleSheets.length],
    };
}"""

DURING_SCRIPT = """() => [
    getComputedStyle(document.querySelector('.bar')).color,
    getComputedStyle(document.querySelector('.bar'), '::before').color,
    getComputedStyle(document.querySelector('.host').shadowRoot.querySelector('b')).color,
    getComputedStyle(document.querySelector('.stubborn')).color,
]"""


@pytest.mark.browser
def test_marking_takes_moves_nothing_and_restoring_leaves_the_page_as_it_was() -> None:
    async def run() -> dict[str, Any]:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            try:
                page = await browser.new_page()
                await page.set_content(ROUND_TRIP)
                await page.emulate_media(media="print")
                before = await page.evaluate(SNAPSHOT_SCRIPT)
                marked = await page.evaluate(
                    MARK_BOXES_SCRIPT,
                    {
                        "attribute": INK_ATTRIBUTE,
                        "base": [5, 243, 43],
                        "noFilter": NO_FILTER_ID,
                    },
                )
                moved = await page.evaluate(LAYOUT_MOVED_SCRIPT)
                during = await page.evaluate(DURING_SCRIPT)
                await page.evaluate(RESTORE_BOXES_SCRIPT)
                # Past any transition the restore could have started.
                await asyncio.sleep(0.1)
                after = await page.evaluate(SNAPSHOT_SCRIPT)
                return {
                    "before": before,
                    "marked": marked,
                    "moved": moved,
                    "during": during,
                    "after": after,
                }
            finally:
                await browser.close()

    seen = asyncio.run(run())
    assert [
        (box["name"], box["position"], box["unmarked"]) for box in seen["marked"]
    ] == [
        ("div.bar", "fixed", None),
        ("div.host", "sticky", None),
        # The page's own inline `!important` beats any sheet, so the colour is
        # written into that attribute in its place (docs/09 P97-3).
        ("div.stubborn", "fixed", None),
        # An `!important` rule in a cascade layer beats an unlayered one, and
        # the element's attribute holds nothing to write over: still unmarked,
        # and said so.
        ("div.layered", "fixed", "div.layered kept color rgb(4, 5, 6)"),
        ("div.empty", "sticky", None),
    ]
    assert seen["moved"] == {"moved": 0, "first": None}
    bar, host, stubborn = (tuple(seen["marked"][index]["code"]) for index in (0, 1, 2))
    assert len({bar, host, stubborn}) == 3
    assert seen["during"] == [
        f"rgb({bar[0]}, {bar[1]}, {bar[2]})",
        f"rgb({bar[0]}, {bar[1]}, {bar[2]})",
        f"rgb({host[0]}, {host[1]}, {host[2]})",
        f"rgb({stubborn[0]}, {stubborn[1]}, {stubborn[2]})",
    ]
    assert seen["after"] == seen["before"]


SLOT = Path(__file__).parent / "fixtures" / "covering_inline_slot_page.html"
VANISHING = Path(__file__).parent / "fixtures" / "covering_vanishing_banner_page.html"
FILTERED = Path(__file__).parent / "fixtures" / "covering_filtered_box_page.html"


def _capture_file(tmp_path: Path, fixture: Path) -> tuple[list[str], str]:
    pdf = tmp_path / "capture.pdf"
    assert main([str(fixture), "--output", str(pdf), "--no-ocr"]) == 0
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
def test_a_box_whose_inline_important_colour_beat_the_sheet_is_measured(
    tmp_path: Path,
) -> None:
    """An ad slot's `background: ... !important` in its own style attribute kept
    its colour, so the box was reported as not marked and never measured, on
    every page it printed on (P97-3)."""
    warnings, _ = _capture_file(tmp_path, SLOT)
    assert not [w for w in warnings if "could not mark" in w], warnings
    [slot] = warnings
    assert slot.startswith(
        "A fixed box, ins#slot-anchor, prints on both pages, and over "
    )
    assert slot.endswith(
        'Pass --exclude "ins#slot-anchor" to capture the page without the box.'
    )


@pytest.mark.browser
def test_a_box_gone_between_the_render_and_the_check_is_not_a_clean_result(
    tmp_path: Path,
) -> None:
    """A banner its own script hid after the render was printed in the PDF and
    gone from the measuring print, which found nothing and said nothing (P97-4)."""
    warnings, text = _capture_file(tmp_path, VANISHING)
    assert "This harbor office banner leaves after one printing." in text
    [changed] = warnings
    assert changed.startswith(
        "WebShot could not check whether a fixed or sticky box prints over the page's "
        "text (1 fixed or sticky box was on the page when it was printed and none "
        "when it was measured, and the page changed between the render and the "
        "check: page 1 of the PDF holds "
    )
    assert changed.endswith(
        " characters the measuring print does not). So the PDF may "
        "show one over its text without a warning."
    )


@pytest.mark.browser
def test_a_filtered_box_printed_as_a_picture_is_not_a_page_that_changed(
    tmp_path: Path,
) -> None:
    """A filtered box prints as a picture in the render and as text when
    measured, its filter swapped for none (P83-1). The render's text must be
    in the measuring print, and the measuring print's text outside the boxes
    in the render; the box's own words are neither (review of P97-4)."""
    warnings, text = _capture_file(tmp_path, FILTERED)
    assert "drawn with a shadow filter" not in text  # printed as a picture
    assert not [w for w in warnings if "page changed" in w], warnings
    [notice] = warnings
    assert notice.startswith("A fixed box, div.notice, prints ")
