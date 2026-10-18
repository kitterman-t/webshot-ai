"""A line break the page lays out reaches the snapshot, and no other (docs/09 P86).

`VISIBLE_JS` keeps an element only if its box has width and height. A `<br>`'s
box is a caret's, as tall as its line and zero wide, so every line break on
every page was dropped from content.html, and the words either side ran
together there and in content.md, content.txt and the chunks: "alpha<br>beta"
read "alphabeta". A CSV cell holding a line break reached the page as
`<td>…<br>…</td>` and lost it the same way. P48-4 keeps a `<br>` that has a
box at all; these tests hold the shapes it was not measured on, the hidden
ones among them.

Once kept, a `<br>` reaches docling's HTML reader, which writes one as U+E000
and turns it back into a line break only in a paragraph and a table cell. With
P48 in and P56 not yet, that character reached every text surface from a list
item or a term. The last test reads the snapshot through the bridge, as
`bundle/build.py` does, and holds what each surface makes of the kept breaks.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from playwright.async_api import async_playwright

from webshot.capture.snapshot import extract_semantic_content
from webshot.extract.chunks import build_chunk_records
from webshot.extract.docling_bridge import extract

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "line_breaks.html"

#: docling's mark for a `<br>` it has not written out (docs/09 P56).
DOCLING_LINE_BREAK = chr(0xE000)

#: Each pair of words a laid-out `<br>` splits, as they read run together.
JOINED = (
    "alphabeta",
    "gammadelta",
    "etatheta",
    "headcell",
    "cell onecell two",
    "onetwo",
    "termline",
    "descline",
)

#: The words the page hides, beside a `<br>` or holding one.
HIDDEN = ("unseen", "undrawn", "words")


def snapshot_html() -> str:
    async def run() -> str:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                page = await browser.new_page()
                await page.goto(FIXTURE.as_uri())
                extracted = await extract_semantic_content(page)
                return str(extracted["html"])
            finally:
                await browser.close()

    return asyncio.run(run())


def snapshot_body() -> str:
    html = snapshot_html()
    return html[html.index("<body>") : html.index("</body>")]


def strings(value: Any) -> Iterator[str]:
    """Every string in a JSON value, keys included, however deep."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)


@pytest.mark.browser
def test_every_line_break_the_page_lays_out_is_in_the_snapshot() -> None:
    body = snapshot_body()
    for held in (
        "alpha<br>beta",
        "<code>gamma<br>delta</code>",
        # Kept by its <br> alone: the span has no box of its own either.
        "eta<span><br></span>theta",
        "head<br>cell",
        "cell one<br>cell two",
        "one<br>two",
        "term<br>line",
        "desc<br>line",
    ):
        assert held in body, (held, body)


@pytest.mark.browser
def test_a_line_break_the_page_does_not_show_stays_out() -> None:
    body = snapshot_body()
    # No box: the page reads "omicronpi", and so does the snapshot.
    assert "omicronpi" in body
    # A <br> inside hidden text does not bring that text back with it.
    assert "unseen" not in body
    assert "undrawn" not in body and "words" not in body
    # <wbr> is where a line may break, not a break: the word stays one.
    assert "longword" in body
    assert re.findall(r"<br>", body) == ["<br>"] * 8, body


@pytest.mark.browser
def test_every_text_surface_keeps_the_breaks_and_nothing_hidden() -> None:
    result = extract(snapshot_html(), [])
    document = json.loads(result.document_json)
    records = build_chunk_records(result.chunk_seeds)
    surfaces = {
        "content.json": "\n".join(strings(document)),
        "content.md": result.markdown,
        "content.txt": result.text,
        "content.doctags": result.doctags,
        "chunks.jsonl": "\n".join(strings(records)),
    }
    for name, surface in surfaces.items():
        # With P48 and before P56, a list item's or a term's <br> reached
        # every surface as docling's mark: "one", U+E000, "two".
        assert DOCLING_LINE_BREAK not in surface, (name, surface)
        for joined in JOINED:
            assert joined not in surface, (name, joined, surface)
        for word in HIDDEN:
            assert word not in surface, (name, word, surface)
    # A paragraph's break, and inline code's, are line breaks in the plain
    # text, not spaces.
    assert "alpha\nbeta" in result.text, result.text
    assert "gamma\ndelta" in result.text, result.text
    # A Markdown row cannot hold a newline, so the cell's is written as spaces
    # in content.md and content.txt; the document keeps it.
    cells = [
        cell["text"]
        for table in document["tables"]
        for cell in table["data"]["table_cells"]
    ]
    assert "cell one\ncell two" in cells, cells
