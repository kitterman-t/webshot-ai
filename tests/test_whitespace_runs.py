"""ASCII whitespace or a line break in formatted text keeps the words apart
(docs/09 P49).

markdownify's `chomp` gives back one space where an inline element's text
began or ended with a plain space, and nothing otherwise.  So a run of
whitespace alone wrote nothing: `This<strong> </strong>course` read
`Thiscourse` in six real syllabi.  A newline or a `<br>` at the edge of bold or
a link was lost the same way, and MarkItDown's `<a>` lost even a plain space
where it writes no markup: with no target, inside `pre`, and where its text is
its address.  The bridge writes each run of ASCII whitespace at an edge, or
alone, as one space, and in `pre` and `code` as the document has it.  A
`<br>` there is the line break it is, written outside the markup (docs/09
P77).

The `whitespace_run_*` fixtures are written by `tools/fixtures/office.py`;
every word in them is made up.  `tests/test_office_formats.py` captures them
and reads content.md and the PDF.
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

from tools.fixtures.office import whitespace_run_lines  # noqa: E402

FIXTURE_ROOT = REPO_ROOT / "tests" / "fixtures" / "office"

#: Every ASCII character `str.strip` takes, which is what `chomp` strips.
#: Derived from Python rather than listed, so none is left out.
ASCII_WHITESPACE = [c for c in map(chr, range(128)) if c.isspace()]

#: What a run can hold that a browser shows as one space between two words:
#: each ASCII whitespace character, and runs of them.
WHITESPACE_ALONE = [*ASCII_WHITESPACE, "  ", " \n ", "\r\n"]
#: A run that holds line breaks alone, and what the bridge writes for it: the
#: line breaks outside the markup, each followed by the space `convert_br`
#: writes, and the whitespace beside them one space (docs/09 P77).
LINE_BREAKS_ALONE = {
    "<br/>": "alpha<br> omega",
    " <br/> ": "alpha <br> omega",
    "<br/><br/>": "alpha<br> <br> omega",
}
#: Where markdownify writes text as it stands, and so a `<br>` as its own
#: line ending, which Markdown reads as a space there.
LITERAL = {"code", "kbd", "samp"}

#: Each inline element whose converter `chomp`s, as HTML around a word, and
#: the three ways MarkItDown's `<a>` returns without its markup.
INLINE = {
    "strong": ("<strong>", "</strong>"),
    "b": ("<b>", "</b>"),
    "em": ("<em>", "</em>"),
    "i": ("<i>", "</i>"),
    "code": ("<code>", "</code>"),
    "kbd": ("<kbd>", "</kbd>"),
    "samp": ("<samp>", "</samp>"),
    "del": ("<del>", "</del>"),
    "s": ("<s>", "</s>"),
    "strike": ("<strike>", "</strike>"),
    "sub": ("<sub>", "</sub>"),
    "sup": ("<sup>", "</sup>"),
    "u": ("<u>", "</u>"),
    "a": ('<a href="https://example.invalid/x">', "</a>"),
    "a-mailto": ('<a href="mailto:x@example.invalid">', "</a>"),
    "a-no-href": ('<a id="x">', "</a>"),
    "a-empty-href": ('<a href="">', "</a>"),
    "nested": ("<strong><em>", "</em></strong>"),
}


class _Upstream(_TextMarkdownify):
    """The bridge's writer with every `chomp`ing converter MarkItDown's."""


for _name in dir(_TextMarkdownify):
    if hasattr(getattr(_TextMarkdownify, _name), "__wrapped__"):
        setattr(_Upstream, _name, getattr(_CustomMarkdownify, _name))


def _markdown(document: str, writer: type[_TextMarkdownify] = _TextMarkdownify) -> str:
    seams = _ListSeams()
    written: str = writer(list_seams=seams).convert_soup(
        BeautifulSoup(document, "html.parser")
    )
    return seams.resolve(written)


def _page(document: str) -> str:
    """The page's HTML for `document`, through the bridge's HTML reader."""
    converted = _TextHtmlConverter().convert(
        io.BytesIO(document.encode("utf-8")), StreamInfo(charset="utf-8")
    )
    return sanitize_markdown_html(
        markdown.markdown(
            converted.markdown,
            extensions=list(MARKDOWN_EXTENSIONS),
            output_format="html5",
        )
    )


def _reads(page: str) -> str:
    """What a reader reads on `page`: its text, a line break and each run of
    whitespace one space, as a browser lays them out on one line.  `~~` is markdownify's strikethrough, which
    Python-Markdown leaves as text; it is taken out so that what is measured
    is the spacing alone."""
    text = html.unescape(re.sub(r"<[^>]+>", "", re.sub(r"<br\s*/?>", "\n", page)))
    return re.sub(r"\s+", " ", text.replace("~~", "")).strip()


def _line(tag: str, lead: str, trail: str) -> str:
    """A paragraph whose words are kept apart only by `lead` and `trail`, at
    the edges of `beta`'s formatting: a plain space stands outside where an
    edge is empty."""
    opening, closing = INLINE[tag]
    before = "alpha" + ("" if lead else " ")
    after = ("" if trail else " ") + "omega"
    return f"<p>{before}{opening}{lead}beta{trail}{closing}{after}</p>"


# --------------------------------------------------------------------------- #
# The writer
# --------------------------------------------------------------------------- #


def test_the_whitespace_measured_is_every_ascii_space() -> None:
    assert {" ", "\t", "\n", "\r", "\x0c"} <= set(ASCII_WHITESPACE)
    assert all(not c.strip() for c in ASCII_WHITESPACE)


@pytest.mark.parametrize("tag", list(INLINE))
def test_whitespace_alone_is_one_space(tag: str) -> None:
    opening, closing = INLINE[tag]
    wrong = []
    for run in WHITESPACE_ALONE:
        document = f"<p>alpha{opening}{run}{closing}omega</p>"
        if _markdown(document).strip() != "alpha omega":
            wrong.append((run, _markdown(document)))
    assert not wrong, wrong


@pytest.mark.parametrize("tag", list(INLINE))
def test_a_line_break_alone_is_written_outside_the_markup(tag: str) -> None:
    opening, closing = INLINE[tag]
    wrong = []
    for run, expected in LINE_BREAKS_ALONE.items():
        document = f"<p>alpha{opening}{run}{closing}omega</p>"
        if tag in LITERAL:
            expected = "alpha omega"
        if _markdown(document).strip() != expected:
            wrong.append((run, _markdown(document)))
    assert not wrong, wrong


@pytest.mark.parametrize("tag", list(INLINE))
def test_a_newline_at_an_edge_is_one_space(tag: str) -> None:
    wrong = []
    for edge in ("\n", "\r\n", "\x0c"):
        for lead, trail in ((edge, ""), ("", edge), (edge, edge)):
            document = _line(tag, lead, trail)
            written = _markdown(document).strip()
            if "\n" in written or _reads(_page(document)) != "alpha beta omega":
                wrong.append((lead, trail, written))
    assert not wrong, wrong


@pytest.mark.parametrize("tag", list(INLINE))
def test_a_br_at_an_edge_is_a_line_break_outside_the_markup(tag: str) -> None:
    """The words either side stay apart, no newline is written, and the page
    holds each `<br>` the edges did, outside the formatting: where the
    formatting held the `<br>`, it held nothing else there."""
    wrong = []
    for edge in ("<br/>", " <br/>", "<br/> "):
        for lead, trail in ((edge, ""), ("", edge), (edge, edge)):
            document = _line(tag, lead, trail)
            written = _markdown(document).strip()
            page = _page(document)
            breaks = 0 if tag in LITERAL else document.count("<br/>")
            if (
                "\n" in written
                or _reads(page) != "alpha beta omega"
                or page.count("<br>") != breaks
                or re.search(r"<(strong|em|del|sub|sup|a)\b[^>]*>\s*<br>", page)
                or re.search(r"<br>\s*</(strong|em|del|sub|sup|a)>", page)
            ):
                wrong.append((lead, trail, written, page))
    assert not wrong, wrong


@pytest.mark.parametrize("tag", list(INLINE))
def test_a_space_at_an_edge_is_kept_where_a_link_writes_no_markup(tag: str) -> None:
    """The plain space `chomp` gives back: MarkItDown's `<a>` threw it away
    with no target, and is held to the rest here."""
    for lead, trail in ((" ", ""), ("", " "), (" ", " "), ("\t", "\t")):
        document = _line(tag, lead, trail)
        assert _reads(_page(document)) == "alpha beta omega", document


def test_a_link_whose_text_is_its_address_keeps_its_edges() -> None:
    # MarkItDown writes `<address>` for it, and returned that without `chomp`'s
    # spaces: `Retrieved from<a href=…> https://…</a>` ran together.  The
    # writer no longer writes that form (docs/09 P75); the edges stay either way.
    address = "https://example.invalid/whitespace"
    document = f'<p>alpha<a href="{address}"> {address} </a>omega</p>'
    assert _markdown(document).strip() == f"alpha [{address}]({address}) omega"
    upstream = _CustomMarkdownify().convert_soup(BeautifulSoup(document, "html.parser"))
    assert upstream.strip() == f"alpha<{address}>omega"
    assert _reads(_page(document)) == f"alpha {address} omega"


@pytest.mark.parametrize("tag", list(INLINE))
def test_an_edge_never_begins_a_line(tag: str) -> None:
    """A newline kept at an edge, beside the newline the next text begins
    with, made a blank line, and a contents list in a real EPUB fell apart
    into paragraphs.  Every edge is one space, so none can begin a line."""
    opening, closing = INLINE[tag]
    items = "".join(
        f"""
  <li><p>
    <em>Part</em>
    <span>{opening}<span>
        <em>{number}</em>
      </span>{closing}</span>
    <em>—Ferns</em>
    : a made-up summary{run}.
  </p></li>"""
        for number, run in (
            ("1", ""),
            ("2", f"{opening}\n{closing}"),
            ("3", f"{opening}<br/>{closing}"),
        )
    )
    document = f"<ul>{items}\n</ul>"
    written = _markdown(document)
    assert "\n\n" not in written.strip(), written
    page = _page(document)
    assert page.count("<li>") == 3 and page.count("</ul>") == 1, page
    assert "<p>" not in page, page


def test_text_in_pre_and_code_keeps_its_edges_as_they_are() -> None:
    for document, expected in (
        ("<pre>alpha<b>  </b>omega</pre>", "alpha  omega"),
        ("<pre>alpha<b>\tbeta\n</b>omega</pre>", "alpha\tbeta\nomega"),
        ("<p><code>alpha<b>  </b>omega</code></p>", "alpha omega"),
    ):
        assert expected in _markdown(document), document
        assert _markdown(document) == _markdown(document, _Upstream), document
    # MarkItDown's `<a>` strips its text before it looks for `pre`.
    link = '<pre>alpha<a href="https://example.invalid/x">  beta\n</a>omega</pre>'
    assert "alpha  beta\nomega" in _markdown(link)
    assert "alphabetaomega" in _markdown(link, _Upstream)


def test_whitespace_alone_in_a_heading_a_cell_and_an_item_is_one_space() -> None:
    for document in (
        "<h2>alpha<strong> </strong>omega</h2>",
        "<table><tr><th>h</th></tr><tr><td>alpha<em>\n</em>omega</td></tr></table>",
        "<ul><li>alpha<b>\t</b>omega</li></ul>",
    ):
        assert "alpha omega" in _reads(_page(document)), document


# --------------------------------------------------------------------------- #
# The fixtures, as the page WebShot captures
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "name",
    [
        "whitespace_run_report.docx",
        "whitespace_run_book.epub",
        "whitespace_run_archive.zip",
    ],
)
def test_each_fixture_line_reads_on_the_page_with_its_words_apart(name: str) -> None:
    """Through the whole of the adapter, MarkItDown's own clean-up included."""
    if name.endswith(".docx") and importlib.util.find_spec("mammoth") is None:
        pytest.skip("mammoth is absent — install webshot[office] to run this")
    materialized = materialize_source((FIXTURE_ROOT / name).as_uri())
    try:
        page = file_url_path(materialized.browser_url).read_text("utf-8")
    finally:
        materialized.cleanup()
    text = _reads(page.split("<main>", 1)[1])
    lines = whitespace_run_lines(name)
    assert len(lines) > 50, "the fixture lost its lines"
    missing = [line.text for line in lines if line.text not in text]
    assert not missing, missing
