"""A `<br>` in a converted document is a line break on the page (docs/09 P77).

markdownify writes a `<br>` as Markdown's hard break, two spaces and a
newline, and MarkItDown strips the end of every line of its Markdown before
WebShot sees it.  Python-Markdown then read a soft break, and the browser drew
a space: `one<br/>two` read `one two`, and a Word file's manual line breaks ran
on as one line.  Two `<br>`s wrote a blank line, which ended a list item and
printed the rest of the list as text, and split bold text so that its `**`
showed.  The bridge writes `<br>` itself, followed by a space, where text
flows, and markdownify's own in a heading, in code, and among a list's or a
table's own children.

The `line_break_*` fixtures are written by `tools/fixtures/office.py`; every
word in them is made up.  `tests/test_office_formats.py` captures them and
reads content.md and the PDF's text and bookmarks.
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
from markitdown import MarkItDown, StreamInfo
from markitdown.converters import HtmlConverter

from webshot.acquire.adapters import (
    MARKDOWN_EXTENSIONS,
    materialize_source,
    sanitize_markdown_html,
)
from webshot.acquire.markitdown_bridge import (
    LINE_BREAK,
    _edges,
    _ListSeams,
    _TextHtmlConverter,
    _TextMarkdownify,
    to_markdown,
)
from webshot.netpolicy import file_url_path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:  # the fixture generator lives under tools/
    sys.path.insert(0, str(REPO_ROOT))

from tools.fixtures.office import line_break_cases  # noqa: E402

FIXTURE_ROOT = REPO_ROOT / "tests" / "fixtures" / "office"
FIXTURES = ["line_break_report.docx", "line_break_book.epub", "line_break_archive.zip"]

#: Block elements, whose edges end a line on the page as a `<br>` does.
_BLOCK_TAG_RE = re.compile(
    r"</?(?:p|div|li|ul|ol|blockquote|h\d|table|thead|tbody|tr|th|td|dl|dt|dd)\b[^>]*>"
)


def _markdown(document: str) -> str:
    """`document` as the bridge's writer writes it, before MarkItDown's clean-up."""
    seams = _ListSeams()
    written: str = _TextMarkdownify(list_seams=seams).convert_soup(
        BeautifulSoup(document, "html.parser")
    )
    return seams.resolve(written)


def _normalized(written: str) -> str:
    """What MarkItDown makes of a converter's Markdown before returning it."""
    lines = "\n".join(line.rstrip() for line in re.split(r"\r?\n", written))
    return re.sub(r"\n{3,}", "\n\n", lines)


def _render(source: str) -> str:
    rendered: str = sanitize_markdown_html(
        markdown.markdown(
            source, extensions=list(MARKDOWN_EXTENSIONS), output_format="html5"
        )
    )
    return rendered


def _page(document: str) -> str:
    """The page's HTML for `document`, through the bridge's HTML reader and
    MarkItDown's clean-up of every line's end, which is what removed the
    hard break."""
    converted = _TextHtmlConverter().convert(
        io.BytesIO(document.encode("utf-8")), StreamInfo(charset="utf-8")
    )
    return _render(_normalized(converted.markdown))


def _upstream_page(document: str) -> str:
    """The same through MarkItDown's own HTML reader, as WebShot read it before."""
    converted = HtmlConverter().convert(
        io.BytesIO(document.encode("utf-8")), StreamInfo(charset="utf-8")
    )
    return _render(_normalized(converted.markdown))


def _lines(page: str) -> list[str]:
    """The page's lines as the capture prints them: a `<br>` or a block's edge
    ends one, other whitespace is a space, and `pre` and `code` keep their own
    lines, as the print CSS's `white-space: pre-wrap` has them.  Empty lines
    are left out."""
    parts = []
    for part in re.split(r"(<pre\b.*?</pre>|<code\b.*?</code>)", page, flags=re.S):
        if part.startswith("<pre"):
            parts.append("\n" + re.sub(r"<[^>]+>", "", part) + "\n")
            continue
        if part.startswith("<code"):
            parts.append(re.sub(r"<[^>]+>", "", part))
            continue
        text = re.sub(r"<br\s*/?>", "\n", re.sub(r"\s+", " ", part))
        parts.append(re.sub(r"<[^>]+>", "", _BLOCK_TAG_RE.sub("\n", text)))
    lines = (
        " ".join(line.split()) for line in html.unescape("".join(parts)).split("\n")
    )
    return [line for line in lines if line]


def _shows(page: str, *lines: str) -> bool:
    """Whether `page` shows `lines` one after another, each a line of its own."""
    shown = _lines(page)
    return any(
        shown[start : start + len(lines)] == list(lines)
        for start in range(len(shown) - len(lines) + 1)
    )


# --------------------------------------------------------------------------- #
# What the bridge corrects
# --------------------------------------------------------------------------- #


def test_markitdown_still_takes_the_hard_break_off() -> None:
    """The premise: markdownify writes Markdown's hard break, and MarkItDown
    strips it from the end of the line.  If either stops, the page shows a
    `<br>` without the bridge, and this correction should be looked at again."""
    document = b"<html><body><p>one<br/>two</p></body></html>"
    written = HtmlConverter().convert(io.BytesIO(document), StreamInfo()).markdown
    assert written == "one  \ntwo"
    result = MarkItDown(enable_plugins=False).convert_stream(
        io.BytesIO(document), stream_info=StreamInfo(extension=".html")
    )
    assert result.markdown == "one\ntwo"
    assert _render(result.markdown) == "<p>one\ntwo</p>"


#: A `<br>` where text flows, the HTML around it, and the lines the page
#: shows.  Each was a space on the page before.
WHERE_TEXT_FLOWS = {
    "paragraph": ("<p>alpha<br/>omega</p>", ["alpha", "omega"]),
    "loose text": ("alpha<br/>omega", ["alpha", "omega"]),
    "division": ("<div>alpha<br/>omega</div>", ["alpha", "omega"]),
    "pretty-printed": ("<p>alpha<br/>\n    omega</p>", ["alpha", "omega"]),
    "item": ("<ul><li>alpha<br/>omega</li></ul>", ["alpha", "omega"]),
    "paragraph in an item": (
        "<ol><li><p>alpha<br/>omega</p></li></ol>",
        ["alpha", "omega"],
    ),
    "quotation": (
        "<blockquote><p>alpha<br/>omega</p></blockquote>",
        ["alpha", "omega"],
    ),
    "table cell": (
        "<table><tr><th>h</th></tr><tr><td>alpha<br/>omega</td></tr></table>",
        ["h", "alpha", "omega"],
    ),
    "header cell": (
        "<table><tr><th>alpha<br/>omega</th></tr><tr><td>x</td></tr></table>",
        ["alpha", "omega", "x"],
    ),
    "term": ("<dl><dt>alpha<br/>omega</dt><dd>x</dd></dl>", ["alpha", "omega", "x"]),
    "definition": (
        "<dl><dt>x</dt><dd>alpha<br/>omega</dd></dl>",
        ["x", "alpha", "omega"],
    ),
    "bold": (
        "<p>alpha <strong>beta<br/>gamma</strong> omega</p>",
        ["alpha beta", "gamma omega"],
    ),
    "link": (
        '<p>alpha <a href="https://example.invalid/b">beta<br/>gamma</a> omega</p>',
        ["alpha beta", "gamma omega"],
    ),
    "underlined": (
        "<p>alpha <u>beta<br/>gamma</u> omega</p>",
        ["alpha beta", "gamma omega"],
    ),
    "caption": (
        '<figure><img src="x.png" alt=""/><figcaption>alpha<br/>omega</figcaption></figure>',
        ["alpha", "omega"],
    ),
    "first": ("<p><br/>alpha omega</p>", ["alpha omega"]),
}


@pytest.mark.parametrize("where", list(WHERE_TEXT_FLOWS))
def test_a_line_break_where_text_flows_is_a_br(where: str) -> None:
    document, lines = WHERE_TEXT_FLOWS[where]
    page = _page(document)
    assert page.count("<br>") == document.count("<br/>"), page
    assert _shows(page, *lines), (page, _lines(page))
    assert "<br>" not in _upstream_page(document)


@pytest.mark.parametrize("where", list(WHERE_TEXT_FLOWS))
def test_a_line_break_writes_no_newline(where: str) -> None:
    """A space follows the `<br>`, so the line breaks it writes are the
    document's own newlines and no more, and none can make a blank line."""
    document, _ = WHERE_TEXT_FLOWS[where]
    without = document.replace("<br/>", "")
    assert _markdown(document).count("\n") == _markdown(without).count("\n"), _markdown(
        document
    )


def test_two_line_breaks_keep_a_list_and_bold_text_whole() -> None:
    """markdownify's two hard breaks were a blank line: the list ended at it
    and its last item printed as text, and the bold text's `**` showed.
    Outside a paragraph's own flow a run is `<br>`s."""
    listed = "<ul><li>alpha<br/><br/>beta</li><li>gamma</li></ul>"
    page = _page(listed)
    assert page.count("<ul>") == 1 and page.count("<li>") == 2, page
    assert "*" not in re.sub(r"<[^>]+>", "", page), page
    assert "* gamma" in _upstream_page(listed)
    bold = "<p>alpha <b>beta<br/><br/>gamma</b> omega</p>"
    page = _page(bold)
    assert re.search(r"<strong>beta<br>\s*<br>\s*gamma</strong>", page), page
    assert "**" in _upstream_page(bold)
    for document in (
        "<dl><dt>t</dt><dd>alpha<br/><br/>omega</dd></dl>",
        "<table><tr><th>h</th></tr><tr><td>alpha<br/><br/>omega</td></tr></table>",
        "<p><span>alpha<br/><br/>omega</span></p>",
    ):
        assert _page(document).count("<br>") == 2, document


@pytest.mark.parametrize(
    "document",
    [
        "<p>alpha<br/><br/>omega</p>",
        "<p>alpha<br/>\n  <br/>\n  omega</p>",
        "<p>alpha<br/><br/><br/>omega</p>",
        "alpha<br/><br/>omega",
        "<blockquote><p>alpha<br/><br/>omega</p></blockquote>",
        "<p>alpha<br/><br/><strong>omega</strong></p>",
    ],
)
def test_two_line_breaks_in_a_paragraph_are_a_paragraph_break_as_before(
    document: str,
) -> None:
    """Two paragraphs show much as the empty line between them does, and
    docling, which drops a `<br>` that touches bold text, keeps them apart in
    content.md: a contact block of bold labels ran into one paragraph."""
    page = _page(document)
    assert page == _upstream_page(document)
    assert re.search(r"<p>alpha</p>\s*<p>(<strong>)?omega", page), page


def test_text_after_a_line_break_is_text() -> None:
    """The text after a `<br>` began a Markdown line under markdownify's hard
    break; it does not now, and what would open a block there is text."""
    page = _page("<p>alpha<br/>- beta<br/>3. gamma<br/># delta<br/>&gt; omega</p>")
    assert page.count("<p>") == 1 and "<li>" not in page and "<h1" not in page
    assert _shows(page, "alpha", "- beta", "3. gamma", "# delta", "> omega")


def test_a_documents_own_br_text_is_text() -> None:
    """`<br>` the document wrote as text is encoded as text is, outside code;
    in code it is the code's, never taken for the line break at an edge."""
    page = _page(
        "<p>alpha &lt;br&gt; beta <code>&lt;br&gt; gamma</code> "
        "<b><code>&lt;br&gt;</code></b> omega</p>"
    )
    assert "<br>" not in page, page
    assert page.count("&lt;br&gt;") == 3, page


# --------------------------------------------------------------------------- #
# Where a line break is what markdownify writes
# --------------------------------------------------------------------------- #


def test_a_line_break_in_a_heading_is_a_space() -> None:
    """Chromium names a heading's PDF bookmark from its text without the
    `<br>` and without the space the browser hides after it, so a `<br>` in a
    heading would have bookmarked `alphaomega`.  The anchor `toc` derives and
    the title read from the first heading are as they were."""
    for document in ("<h1>alpha<br/>omega</h1>", "<h3>alpha<b><br/></b>omega</h3>"):
        page = _page(document)
        assert "<br>" not in page, page
        assert 'id="alpha-omega"' in page and _shows(page, "alpha omega"), page
    assert _page("<h2>alpha<br/>omega</h2>") == _upstream_page(
        "<h2>alpha<br/>omega</h2>"
    )


def test_a_line_break_in_code_is_what_markdownify_writes() -> None:
    """Code holds no HTML: a newline, which Markdown keeps in a code block and
    the capture prints (`white-space: pre-wrap`).  Inline code is written on
    one line, so that no line of a document's code can open a block, and
    every line break in it is a space (docs/09 P59): markdownify's newline
    for a `<br>` too."""
    document = "<pre>alpha<br/>omega</pre>"
    page = _page(document)
    assert page == _upstream_page(document), document
    assert _shows(page, "alpha", "omega"), page
    page = _page("<p>x <code>alpha<br/>omega</code> y</p>")
    assert _shows(page, "x alpha omega y"), page
    code = re.search(r"<code>(.*?)</code>", page, re.S)
    assert code is not None and "\n" not in code[1], page
    # At an edge, P49's one space, which markdownify's `chomp` dropped.
    assert _shows(_page("<p>x <kbd>alpha<br/></kbd>omega</p>"), "x alpha omega")


@pytest.mark.parametrize(
    "document",
    [
        "<ul><li>alpha</li><br/><li>omega</li></ul>",
        "<ol><br/><li>alpha</li></ol>",
        "<dl><dt>alpha</dt><br/><dd>omega</dd></dl>",
        "<table><tr><td>alpha</td></tr><br/><tr><td>omega</td></tr></table>",
    ],
)
def test_a_line_break_among_items_or_rows_is_what_markdownify_writes(
    document: str,
) -> None:
    """A space there would join the next item, definition or row to the line
    the `<br>` is on."""
    assert _page(document) == _upstream_page(document)


def test_a_csv_cell_reads_its_line_break_as_before(tmp_path: Path) -> None:
    """The bridge writes a cell's line break as `<br>` itself (docs/09
    P10-23), straight into MarkItDown's table, and that does not move."""
    source = tmp_path / "cells.csv"
    source.write_text('name,note\nalpha,"one\ntwo"\n', encoding="utf-8")
    page = _render(to_markdown(source, suffix=".csv").markdown)
    assert "<td>one<br>two</td>" in page, page


# --------------------------------------------------------------------------- #
# The edges of formatted text
# --------------------------------------------------------------------------- #


def test_the_edges_come_off_with_their_line_breaks() -> None:
    br = LINE_BREAK
    assert _edges(f"{br} beta{br} ", breaks=True) == (f"{br} ", "beta", f"{br} ")
    assert _edges(f" \n{br} {br} beta gamma \u00a0", breaks=True) == (
        f" \n{br} {br} ",
        "beta gamma",
        " \u00a0",
    )
    assert _edges(f"beta{br} gamma", breaks=True) == ("", f"beta{br} gamma", "")
    assert _edges(f"{br} {br} ", breaks=True) == (f"{br} {br} ", "", "")
    assert _edges(f"{br}x{br}", breaks=False) == ("", f"{br}x{br}", "")
    assert _edges("<brx> beta", breaks=True) == ("", "<brx> beta", "")


# --------------------------------------------------------------------------- #
# The fixtures, as the page WebShot captures
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("name", FIXTURES)
def test_each_fixture_case_shows_its_lines(name: str) -> None:
    """Through the whole of the adapter, MarkItDown's own clean-up included."""
    if name.endswith(".docx") and importlib.util.find_spec("mammoth") is None:
        pytest.skip("mammoth is absent — install webshot[office] to run this")
    materialized = materialize_source((FIXTURE_ROOT / name).as_uri())
    try:
        page = file_url_path(materialized.browser_url).read_text("utf-8")
    finally:
        materialized.cleanup()
    page = page.split("<main>", 1)[1]
    cases = line_break_cases(name)
    assert len(cases) > 12, "the fixture lost its cases"
    wrong = [
        (case.label, _lines(page))
        for case in cases
        if not _shows(page, *(" ".join(line) for line in case.lines))
    ]
    assert not wrong, wrong
    if not name.endswith(".docx"):
        assert len(re.findall(r"<li>", page)) == 5, "a list came apart"
