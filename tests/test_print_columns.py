"""Text a page prints in columns is named, with the rule that prints it in one
(docs/09 P135-5).

A consulting firm's print stylesheet set its article in two columns, and a
reader that took the PDF's text line by line across the page joined each line
of one column to the line beside it. The layout is the page's, and is kept;
the warning names the block and gives the `--css` rule. A short list set in
columns, and the footer's links, are not an article's text.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from webshot.cli import main
from webshot.render.columns import ColumnBlock, column_blocks, columns_warning

FIXTURE = Path(__file__).parent / "fixtures" / "print_columns_page.html"

RULE = "div.story-body { columns: auto !important; }"


def _warnings(tmp_path: Path, *args: str, fixture: Path = FIXTURE) -> list[str]:
    pdf = tmp_path / "capture.pdf"
    assert main([str(fixture), "--output", str(pdf), "--no-ocr", *args]) == 0
    manifest = json.loads(
        (pdf.with_suffix(".ai") / "manifest.json").read_text(encoding="utf-8")
    )
    return list(manifest["warnings"])


def _characters() -> int:
    """The story's text without spaces, as the browser's `innerText` has it."""
    import re

    html = FIXTURE.read_text(encoding="utf-8")
    story = html.split('<div class="story-body">', 1)[1].split("</div>", 1)[0]
    return len(re.sub(r"\s+", "", re.sub(r"<[^>]+>", "", story)))


@pytest.mark.browser
@pytest.mark.parametrize("mode", ["clean", "faithful"])
def test_an_article_printed_in_columns_is_named(tmp_path: Path, mode: str) -> None:
    assert _warnings(tmp_path, "--mode", mode) == [
        "The page prints a block of its text in columns: div.story-body, in 2 "
        f"columns, {_characters():,} characters. The PDF sets its columns side by "
        "side, as the page does, but a reader that takes a PDF's text line by line "
        "across the page joins each line of one column to the line beside it in "
        "the next, as pdftotext did on such a page, and so does copying a passage "
        "that runs on to the next line. The bundle holds the text in order. To "
        f"print it in one column, pass --css with a file holding {RULE}."
    ]


@pytest.mark.browser
def test_the_rule_the_warning_gives_prints_it_in_one(tmp_path: Path) -> None:
    css = tmp_path / "one-column.css"
    css.write_text(RULE + "\n", encoding="utf-8")
    assert _warnings(tmp_path, "--css", str(css)) == []


@pytest.mark.browser
def test_on_screen_the_article_is_one_column(tmp_path: Path) -> None:
    assert _warnings(tmp_path, "--media", "screen") == []


def test_more_blocks_than_a_warning_names_are_counted() -> None:
    blocks = [ColumnBlock(f"div.part-{n}", 2, 500 + n) for n in range(4)]
    warning = columns_warning(blocks)
    assert warning is not None
    assert warning.startswith(
        "The page prints 4 blocks of its text in columns: div.part-0, in 2 "
        "columns, 500 characters; div.part-1, in 2 columns, 501 characters; "
        "div.part-2, in 2 columns, 502 characters, and 1 more. The PDF sets their "
    )
    assert warning.endswith(
        "To print them in one column, pass --css with a file holding "
        "div.part-0, div.part-1, div.part-2 { columns: auto !important; }."
    )
    assert columns_warning([]) is None


def test_an_answer_of_another_shape_is_no_blocks() -> None:
    assert column_blocks(None) == ()
    assert column_blocks([{"name": "div.a", "columns": 2, "characters": 450}, {}]) == (
        ColumnBlock("div.a", 2, 450),
    )


SCREEN = Path(__file__).parent / "fixtures" / "screen_columns_page.html"


def _story_starts(pdf: Path) -> tuple[float, float]:
    """Where the story's first and last paragraphs start across the page."""
    from webshot.render.pdfium_bridge import read_ink

    [page] = read_ink(pdf.read_bytes())
    text = "".join(glyph.char for glyph in page.glyphs)
    first = text.index("The harbor office keeps")
    last = text.index("Until then the office")
    return page.glyphs[first].x, page.glyphs[last].x


@pytest.mark.browser
def test_under_screen_media_the_rule_the_warning_gives_prints_one_column(
    tmp_path: Path,
) -> None:
    """Columns the page sets for every medium print under `--media screen`,
    and the rule the warning gave, wrapped in a print-only media query, did
    nothing there (review of P135-5). The rule names no media now: applied,
    the last paragraph starts where the first does."""
    [warning] = _warnings(tmp_path / "two", "--media", "screen", fixture=SCREEN)
    assert warning.endswith(f"pass --css with a file holding {RULE}.")
    first, last = _story_starts(tmp_path / "two" / "capture.pdf")
    assert last - first > 100
    css = tmp_path / "one-column.css"
    css.write_text(RULE + "\n", encoding="utf-8")
    one = _warnings(
        tmp_path / "one", "--media", "screen", "--css", str(css), fixture=SCREEN
    )
    assert one == []
    first, last = _story_starts(tmp_path / "one" / "capture.pdf")
    assert abs(last - first) < 2


def test_a_check_that_could_not_read_the_columns_says_so() -> None:
    assert columns_warning([], "the script that reads them failed: boom") == (
        "WebShot could not check whether the page prints its text in columns (the "
        "script that reads them failed: boom), so a PDF whose text a reader taking "
        "it line by line across the page would join from two columns may carry no "
        "warning."
    )


def _measure(fixture: Path, monkeypatch: pytest.MonkeyPatch, **scripts: str) -> object:
    """`find_clipped_text` over `fixture` printed at 779px, with any of its
    scripts replaced."""
    import asyncio

    from playwright.async_api import async_playwright

    from webshot.config import CaptureOptions
    from webshot.render import clipping

    for name, script in scripts.items():
        monkeypatch.setattr(clipping, name, script)

    async def run() -> object:
        options = CaptureOptions(
            source=str(fixture), output=fixture.with_suffix(".pdf")
        )
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            try:
                page = await browser.new_page(viewport={"width": 1440, "height": 1000})
                await page.goto(fixture.as_uri())
                await page.emulate_media(media="print")
                return await clipping.find_clipped_text(page, options, 779)
            finally:
                await browser.close()

    return asyncio.run(run())


@pytest.mark.browser
def test_a_column_check_that_fails_keeps_the_clipping_check_s_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """In one `try`, the column script's failure threw the clipping
    measurement away and said the layout could not be measured (review of
    P135-5)."""
    from webshot.render.clipping import PrintClipping

    found = _measure(
        Path(__file__).parent / "fixtures" / "clipping_short_window_page.html",
        monkeypatch,
        COLUMNS_SCRIPT="(minimum) => { throw new Error('boom'); }",
    )
    assert isinstance(found, PrintClipping)
    assert found.width == 779
    assert found.elements >= 1
    assert found.columns == ()
    assert found.columns_unread is not None and "boom" in found.columns_unread


@pytest.mark.browser
def test_a_clipping_check_that_fails_still_reads_the_columns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """And the other way: the clipping measurement's failure does not keep
    the columns from being read."""
    from webshot.render.clipping import PrintClipping

    found = _measure(
        FIXTURE,
        monkeypatch,
        CLIPPED_TEXT_SCRIPT="(args) => { throw new Error('boom'); }",
    )
    assert isinstance(found, PrintClipping)
    assert found.width is None
    assert [block.name for block in found.columns] == ["div.story-body"]
    assert found.columns_unread is None


@pytest.mark.browser
def test_a_window_with_no_size_says_the_columns_were_not_read() -> None:
    """With no layout at the printed width there is none to read them in."""
    import asyncio

    from playwright.async_api import async_playwright

    from webshot.config import CaptureOptions
    from webshot.render.clipping import PrintClipping, find_clipped_text

    async def run() -> PrintClipping:
        options = CaptureOptions(
            source=str(FIXTURE), output=FIXTURE.with_suffix(".pdf")
        )
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            try:
                page = await browser.new_page(no_viewport=True)
                await page.goto(FIXTURE.as_uri())
                return await find_clipped_text(page, options, 779)
            finally:
                await browser.close()

    found = asyncio.run(run())
    assert columns_warning(found.columns, found.columns_unread) == (
        "WebShot could not check whether the page prints its text in columns (the "
        "window has no fixed size to lay the page out in), so a PDF whose text a "
        "reader taking it line by line across the page would join from two columns "
        "may carry no warning."
    )
