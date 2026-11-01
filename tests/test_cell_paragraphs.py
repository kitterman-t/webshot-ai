"""Each paragraph in a table cell is a line of the cell (docs/09 P111).

markdownify writes a `<p>` or `<div>` in a table cell as the space around it,
since a Markdown row is one line, and the browser draws a line at each edge
of one.  So a Word cell's paragraphs ran on as one line, and a code box, which
Word users make as a one-cell table with a paragraph a line of code, printed
as one line of code that does not run.  The bridge writes a `<br>` where the
browser breaks, the line break P77 writes for Word's `w:br`.  A cell of one
paragraph is written as before.

`cell_paragraphs_report.docx` is written by `tools/fixtures/office.py`; every
word in it is made up.  `tests/test_office_formats.py` captures it and reads
the PDF.
"""

from __future__ import annotations

import html
import importlib.util
import io
import re
import sys
from pathlib import Path

import markdown  # type: ignore[import-untyped]
import pytest
from bs4 import BeautifulSoup
from markitdown import StreamInfo
from markitdown.converters import HtmlConverter
from markitdown.converters._markdownify import _CustomMarkdownify

from webshot.acquire.adapters import (
    MARKDOWN_EXTENSIONS,
    materialize_source,
    sanitize_markdown_html,
)
from webshot.acquire.markitdown_bridge import (
    _ListSeams,
    _TextHtmlConverter,
    _TextMarkdownify,
)
from webshot.netpolicy import file_url_path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:  # the fixture generator lives under tools/
    sys.path.insert(0, str(REPO_ROOT))

from tools.fixtures.office import (  # noqa: E402
    CELL_PARAGRAPH_TABLES,
    cell_paragraph_lines,
)

FIXTURE = REPO_ROOT / "tests" / "fixtures" / "office" / "cell_paragraphs_report.docx"


class _WriterBefore(_TextMarkdownify):
    """The bridge's writer without this correction: markdownify's own
    paragraphs and divisions."""

    convert_p = _CustomMarkdownify.convert_p
    convert_div = _CustomMarkdownify.convert_div
    convert_article = _CustomMarkdownify.convert_div
    convert_section = _CustomMarkdownify.convert_div


def _markdown(document: str, writer: type[_TextMarkdownify] = _TextMarkdownify) -> str:
    """`document` as the bridge's writer writes it, before MarkItDown's clean-up."""
    seams = _ListSeams()
    written: str = writer(list_seams=seams).convert_soup(
        BeautifulSoup(document, "html.parser")
    )
    return seams.resolve(written)


def _render(source: str) -> str:
    rendered: str = sanitize_markdown_html(
        markdown.markdown(
            source, extensions=list(MARKDOWN_EXTENSIONS), output_format="html5"
        )
    )
    return rendered


def _page(document: str, converter: type[HtmlConverter] = _TextHtmlConverter) -> str:
    """The page's HTML for `document`, through the bridge's HTML reader, or
    through MarkItDown's own as WebShot read it before."""
    converted = converter().convert(
        io.BytesIO(document.encode("utf-8")), StreamInfo(charset="utf-8")
    )
    return _render(converted.markdown)


def _cells(page: str) -> list[list[str]]:
    """Each body cell of the page's tables, as the lines the browser draws:
    a `<br>` ends one, other whitespace is a space, empty lines are left out."""
    cells = []
    for cell in re.findall(r"<td\b[^>]*>(.*?)</td>", page, flags=re.S):
        text = re.sub(r"<br\s*/?>", "\n", re.sub(r"\s+", " ", cell))
        lines = (
            " ".join(line.split())
            for line in html.unescape(re.sub(r"<[^>]+>", "", text)).split("\n")
        )
        cells.append([line for line in lines if line])
    return cells


def _table(*cells: str) -> str:
    return (
        "<table><tr>" + "".join(f"<td>{cell}</td>" for cell in cells) + "</tr></table>"
    )


# --------------------------------------------------------------------------- #
# What the bridge corrects
# --------------------------------------------------------------------------- #


def test_markdownify_still_writes_a_cells_paragraphs_on_one_line() -> None:
    """The premise: MarkItDown's own reader runs a cell's paragraphs together.
    If it stops, this correction should be looked at again."""
    document = _table("<p>alpha</p><p>omega</p>")
    assert _cells(_page(document, HtmlConverter)) == [["alpha omega"]]


#: Paragraphs and divisions in a cell, with what stands around them, and the
#: lines the browser draws for the cell.  Each was one line on the page.
IN_A_CELL = {
    "paragraphs": ("<p>alpha</p><p>beta</p><p>omega</p>", ["alpha", "beta", "omega"]),
    "pretty-printed": (
        "\n  <p>alpha</p>\n  <p>omega</p>\n",
        ["alpha", "omega"],
    ),
    "divisions": ("<div>alpha</div><div>omega</div>", ["alpha", "omega"]),
    "nested divisions": (
        "<div><p>alpha</p><p>beta</p></div><div>omega</div>",
        ["alpha", "beta", "omega"],
    ),
    "text before": ("alpha<p>omega</p>", ["alpha", "omega"]),
    "text after": ("<p>alpha</p>omega", ["alpha", "omega"]),
    "text around": ("alpha <p>beta</p> omega", ["alpha", "beta", "omega"]),
    "formatted": (
        "<p><strong>alpha</strong></p><p><em>omega</em></p>",
        ["alpha", "omega"],
    ),
    "formatted before": ("<strong>alpha</strong><p>omega</p>", ["alpha", "omega"]),
    "a line break in one": (
        "<p>alpha<br/>beta</p><p>omega</p>",
        ["alpha", "beta", "omega"],
    ),
    "an empty one between": ("<p>alpha</p><p></p><p>omega</p>", ["alpha", "omega"]),
    "a comment between": ("<p>alpha</p><!-- c --><p>omega</p>", ["alpha", "omega"]),
    "code lines": (
        "<p>import alpakit as ak</p><p>print(ak.version)</p>",
        ["import alpakit as ak", "print(ak.version)"],
    ),
    # A block that is all an inline element holds is beside what is beside
    # the element: with only its own siblings looked at, `alpha` and `omega`
    # ran together.
    "in a span": ("alpha<span><p>omega</p></span>", ["alpha", "omega"]),
    "in bold": ("alpha<b><p>omega</p></b>", ["alpha", "omega"]),
    "link cards": (
        "<a href='https://example.invalid/a'><div>alpha</div></a>"
        "<a href='https://example.invalid/o'><div>omega</div></a>",
        ["alpha", "omega"],
    ),
    "a span after": ("<p>alpha</p><span><p>omega</p></span>", ["alpha", "omega"]),
    "text in a span before": (
        "<span>alpha<p>beta</p></span><p>omega</p>",
        ["alpha", "beta", "omega"],
    ),
}


@pytest.mark.parametrize("where", list(IN_A_CELL))
def test_each_paragraph_in_a_cell_is_a_line(where: str) -> None:
    content, lines = IN_A_CELL[where]
    page = _page(_table(content))
    assert _cells(page) == [lines], page
    before = _cells(_render(_markdown(_table(content), _WriterBefore)))
    assert len(before[0]) < len(lines), before


@pytest.mark.parametrize("wrapper", ["div", "section", "article", "blockquote"])
@pytest.mark.parametrize("where", list(IN_A_CELL))
def test_what_holds_the_table_changes_no_cell_line(where: str, wrapper: str) -> None:
    """Nothing above the cell is the cell's: a table in a `<div>`, as an HTML
    page or an EPUB chapter has one, ran `<p>alpha</p>omega` together again,
    the `<div>` taken for a block holding `omega`."""
    content, lines = IN_A_CELL[where]
    page = _page(f"<{wrapper}>{_table(content)}</{wrapper}>")
    assert _cells(page) == [lines], page


@pytest.mark.parametrize("where", list(IN_A_CELL))
def test_a_cells_lines_keep_the_row_one_line(where: str) -> None:
    """The breaks are `<br>`s, followed by a space, so the row is still one
    line of Markdown and the table is still a table."""
    content, _ = IN_A_CELL[where]
    written = _markdown(_table(content))
    rows = [line for line in written.splitlines() if line.startswith("|")]
    assert len(rows) == 3, written


@pytest.mark.parametrize(
    ("content", "breaks"),
    [
        ("alpha<br/><p>omega</p>", 1),
        ("<p>alpha</p><br/>omega", 1),
        # A `<br>` that ends a block's last line is the end of the line the
        # browser draws, as rich-text editors write one: a second made a
        # blank line.
        ("<p>alpha<br/></p><p>omega</p>", 1),
        ("<p>alpha<br/></p>omega", 1),
        ("<div><p>alpha<br/></p></div><p>omega</p>", 1),
        # A paragraph holding only a `<br>` is an empty line, and one.
        ("<p>alpha</p><p><br/></p><p>omega</p>", 2),
    ],
)
def test_a_line_break_beside_a_paragraph_is_its_only_break(
    content: str, breaks: int
) -> None:
    """The `<br>` already ends the line, and another would draw an empty one."""
    page = _page(_table(content))
    assert page.count("<br>") == breaks, page
    assert _cells(page) == [["alpha", "omega"]], page


def test_a_header_cell_and_each_cell_of_a_row_keep_their_own_lines() -> None:
    document = (
        "<table><tr><th><p>alpha</p><p>beta</p></th><th>gamma</th></tr>"
        "<tr><td><p>delta</p><p>epsilon</p></td><td><p>zeta</p></td></tr></table>"
    )
    page = _page(document)
    assert re.search(
        r"<th[^>]*>alpha\s*<br>\s*beta</th>\s*<th[^>]*>gamma</th>", page
    ), page
    assert _cells(page) == [["delta", "epsilon"], ["zeta"]], page


# --------------------------------------------------------------------------- #
# What is written as before
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "content",
    [
        "<p>alpha</p>",
        "\n  <p>alpha omega</p>\n",
        "<div>alpha</div>",
        "<div><p>alpha</p></div>",
        "<p></p>",
        "alpha omega",
        "alpha<br/>omega",
    ],
)
def test_a_cell_of_one_paragraph_is_written_as_before(content: str) -> None:
    document = _table(content, "<p>beta</p>")
    assert _markdown(document) == _markdown(document, _WriterBefore)


@pytest.mark.parametrize(
    "document",
    [
        "<p>alpha</p><p>omega</p>",
        "<div><p>alpha</p><p>omega</p></div>",
        "<ul><li><p>alpha</p><p>omega</p></li></ul>",
        "<blockquote><p>alpha</p><p>omega</p></blockquote>",
    ],
)
def test_paragraphs_outside_a_table_are_written_as_before(document: str) -> None:
    assert _markdown(document) == _markdown(document, _WriterBefore)


@pytest.mark.parametrize(
    ("content", "lines"),
    [
        ("<blockquote><p>alpha</p><p>omega</p></blockquote>", ["alpha", "omega"]),
        ("<ul><li><p>alpha</p><p>omega</p></li></ul>", ["* alpha", "omega"]),
    ],
)
def test_paragraphs_in_an_item_or_a_quotation_in_a_cell_are_lines(
    content: str, lines: list[str]
) -> None:
    """A quotation's or an item's paragraphs are lines of the cell, as the
    browser draws them, where they ran on as one before."""
    page = _page(_table(content))
    assert _cells(page) == [lines], page
    assert len(_cells(_render(_markdown(_table(content), _WriterBefore)))[0]) == 1


@pytest.mark.parametrize(
    "content",
    [
        "<ul><li>alpha</li><li>omega</li></ul>",
        "<ol><li><p>alpha</p></li><li><p>omega</p></li></ol>",
        "<blockquote><p>alpha</p></blockquote>",
        "alpha<blockquote><p>beta</p></blockquote>omega",
        "<pre>alpha\nomega</pre>",
    ],
)
def test_a_list_a_quotation_or_code_in_a_cell_is_otherwise_as_before(
    content: str,
) -> None:
    """Items, a quotation's edges and code are written as markdownify writes
    them in a cell: a paragraph there is all its item or quotation holds, so
    nothing stands beside it."""
    document = _table(content)
    assert _markdown(document) == _markdown(document, _WriterBefore)


def test_a_heading_in_a_cell_is_one_line() -> None:
    """A heading is one line wherever it stands (docs/09 P77-2)."""
    page = _page(_table("<h3><p>alpha</p><p>omega</p></h3>"))
    assert "<br" not in page, page
    assert _cells(page) == [["alpha omega"]], page


def test_code_in_a_cell_is_the_codes_own() -> None:
    """In code a block is the code's text, which P59 writes on one line."""
    document = _table("<pre><p>alpha</p><p>omega</p></pre>")
    assert _markdown(document) == _markdown(document, _WriterBefore)


# --------------------------------------------------------------------------- #
# The fixture, as the page WebShot captures
# --------------------------------------------------------------------------- #


def test_the_fixtures_cells_show_their_lines() -> None:
    """Through the whole of the adapter, mammoth and MarkItDown's clean-up
    included: each cell's lines are its paragraphs and its `w:br`s, and an
    empty paragraph, which mammoth leaves out, is no line."""
    if importlib.util.find_spec("mammoth") is None:
        pytest.skip("mammoth is absent — install webshot[office] to run this")
    materialized = materialize_source(FIXTURE.as_uri())
    try:
        page = file_url_path(materialized.browser_url).read_text("utf-8")
    finally:
        materialized.cleanup()
    expected = [
        cell_paragraph_lines(cell)
        for table in CELL_PARAGRAPH_TABLES
        for row in table
        for cell in row
    ]
    assert any(len(lines) > 2 for lines in expected), "the fixture lost its code box"
    assert any(len(lines) == 1 for lines in expected), "the fixture lost its control"
    main = page.split("<main>", 1)[1]
    assert _cells(main) == expected
    # No empty line: each `<br>` ends one of the cell's lines.
    cells = re.findall(r"<td\b[^>]*>(.*?)</td>", main, flags=re.S)
    assert [cell.count("<br>") for cell in cells] == [len(e) - 1 for e in expected]
