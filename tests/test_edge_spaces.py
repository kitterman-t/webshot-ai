"""A Unicode space at the edge of formatted text stays on the page (docs/09 P43).

markdownify's `chomp` takes an inline element's edges with `str.strip()`,
which removes every Unicode space, and puts back one ASCII space where the text
began or ended with one.  So a real Word file's
`called<strong>&nbsp;SALT.&nbsp;</strong>We'll` read `calledSALT.We'll`.  The
bridge keeps such an edge as the document has it, outside the markup, and
writes an edge of plain spaces as one space, as `chomp` did.  ASCII whitespace
`chomp` dropped is `tests/test_whitespace_runs.py`'s (docs/09 P49).

The `edge_space_*` fixtures are written by `tools/fixtures/office.py`; every
word in them is made up.  `tests/test_office_formats.py` captures them and
reads content.md and the PDF.
"""

from __future__ import annotations

import html
import importlib.util
import inspect
import io
import re
import sys
from pathlib import Path

import markdown  # type: ignore[import-untyped]
import pytest
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

from tools.fixtures.office import EDGE_SPACES, edge_space_lines  # noqa: E402

FIXTURE_ROOT = REPO_ROOT / "tests" / "fixtures" / "office"

#: Every space `str.strip` removes that is not ASCII: what `chomp` lost.
#: Derived from Python rather than listed, so none is left out.
UNICODE_SPACES = [
    chr(point) for point in range(0x80, sys.maxunicode + 1) if chr(point).isspace()
]

#: Every converter in MarkItDown's writer that `chomp`s its text, read from
#: the source of each, so that one a release adds is held here too.
CHOMPING = sorted(
    name
    for name in dir(_CustomMarkdownify)
    if name.startswith("convert_")
    and "chomp(" in inspect.getsource(getattr(_CustomMarkdownify, name))
)

#: Each inline element markdownify writes, as HTML around a word.
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
    "nested": ("<strong><em>", "</em></strong>"),
}


class _Upstream(_TextMarkdownify):
    """The bridge's writer as it was: every `chomp`ing converter MarkItDown's."""


for _name in CHOMPING:
    setattr(_Upstream, _name, getattr(_CustomMarkdownify, _name))


def _markdown(document: str, writer: type[_TextMarkdownify] = _TextMarkdownify) -> str:
    from bs4 import BeautifulSoup

    seams = _ListSeams()
    written = writer(list_seams=seams).convert_soup(
        BeautifulSoup(document, "html.parser")
    )
    return seams.resolve(written)


def _page_text(document: str) -> str:
    """What the page reads for `document`, through the bridge's HTML reader.

    `~~` is markdownify's strikethrough, which Python-Markdown leaves as text;
    it is taken out so that what is measured is the spacing alone.
    """
    converted = _TextHtmlConverter().convert(
        io.BytesIO(document.encode("utf-8")), StreamInfo(charset="utf-8")
    )
    rendered = sanitize_markdown_html(
        markdown.markdown(
            converted.markdown,
            extensions=list(MARKDOWN_EXTENSIONS),
            output_format="html5",
        )
    )
    return html.unescape(re.sub(r"<[^>]+>", "", rendered)).replace("~~", "")


def _line(tag: str, space: str, where: str) -> tuple[str, str]:
    """A paragraph kept apart only by `space` at `where`, and how it must read."""
    opening, closing = INLINE[tag]
    if where == "only":
        return f"<p>alpha{opening}{space}{closing}omega</p>", f"alpha{space}omega"
    lead = space if where in ("lead", "both") else ""
    trail = space if where in ("trail", "both") else ""
    before = "alpha" + ("" if lead else " ")
    after = ("" if trail else " ") + "omega"
    return (
        f"<p>{before}{opening}{lead}beta{trail}{closing}{after}</p>",
        f"{before}{lead}beta{trail}{after}",
    )


# --------------------------------------------------------------------------- #
# The writer
# --------------------------------------------------------------------------- #


def test_every_converter_that_chomps_keeps_its_edge_spaces() -> None:
    # An empty list would hold nothing: `chomp` must still be found where it is.
    assert {"convert_a", "convert_b", "convert_code", "convert_u"} <= set(CHOMPING)
    for name in CHOMPING:
        converter = getattr(_TextMarkdownify, name)
        assert getattr(converter, "__wrapped__", None) is getattr(
            _CustomMarkdownify, name
        ), f"{name} chomps its text and does not keep a Unicode space at its edge"


def test_the_spaces_measured_are_every_unicode_space() -> None:
    assert "\N{NO-BREAK SPACE}" in UNICODE_SPACES
    assert "\N{IDEOGRAPHIC SPACE}" in UNICODE_SPACES
    assert set(EDGE_SPACES.values()) <= set(UNICODE_SPACES)
    assert all(not space.isascii() for space in UNICODE_SPACES)


@pytest.mark.parametrize("tag", list(INLINE))
def test_a_unicode_space_at_an_edge_keeps_the_words_apart(tag: str) -> None:
    wrong = []
    for space in UNICODE_SPACES:
        for where in ("lead", "trail", "both", "only"):
            document, expected = _line(tag, space, where)
            text = _page_text(document)
            if text != expected:
                wrong.append((f"U+{ord(space):04X}", where, expected, text))
    assert not wrong, wrong


@pytest.mark.parametrize("tag", [tag for tag in INLINE if tag != "a-no-href"])
def test_an_edge_of_spaces_or_none_is_written_as_it_was(tag: str) -> None:
    """An edge `chomp` gave back right is written byte for byte as before.

    A newline, a `<br>` or whitespace alone at an edge, which `chomp` dropped,
    and the link with no target, which dropped even a plain space, are
    `tests/test_whitespace_runs.py`'s (docs/09 P49).
    """
    opening, closing = INLINE[tag]
    documents = [
        _line(tag, space, where)[0]
        for space in (" ", "\t")
        for where in ("lead", "trail", "both")
    ]
    documents.append(_line(tag, "", "only")[0])
    # Spaces that are not whitespace to Python.
    documents += [
        f"<p>alpha{opening}\N{ZERO WIDTH SPACE}beta\N{ZERO WIDTH SPACE}{closing}omega</p>",
        f"<p>alpha{opening}\N{ZERO WIDTH NO-BREAK SPACE}beta{closing}omega</p>",
    ]
    for document in documents:
        assert _markdown(document) == _markdown(document, _Upstream), document


@pytest.mark.parametrize("space", ["\N{NO-BREAK SPACE}", "\N{IDEOGRAPHIC SPACE}"])
def test_text_markdown_keeps_literal_is_written_as_the_document_has_it(
    space: str,
) -> None:
    """Inside `pre`, `code`, `kbd` or `samp` markdownify writes text as it is,
    so the edges were already the document's — except a link's: MarkItDown's
    `convert_a` strips its text before it looks for `pre`, and now keeps the
    space."""
    for document in (
        f"<pre>alpha<b>{space}beta{space}</b>omega</pre>",
        f"<p><code>alpha<b>{space}beta{space}</b>omega</code></p>",
    ):
        assert f"alpha{space}beta{space}omega" in _markdown(document), document
        assert _markdown(document) == _markdown(document, _Upstream), document
    link = f'<pre>alpha<a href="https://example.invalid/x">{space}beta</a></pre>'
    assert f"alpha{space}beta" in _markdown(link)
    assert "alphabeta" in _markdown(link, _Upstream)


@pytest.mark.parametrize("tag", ["code", "kbd", "samp"])
def test_inline_code_keeps_its_edges_on_its_one_line(tag: str) -> None:
    """Inline code keeps a Unicode space at its edges (docs/09 P43) and is
    written on one line (docs/09 P59), both at once.  A code converter of the
    writer's own, which put the code on one line, took the place of the one
    that keeps the edges, and the words either side ran together again."""
    nbsp = "\N{NO-BREAK SPACE}"
    opening, closing = INLINE[tag]
    document = f"<p>alpha{opening}{nbsp}beta\n# gamma\ndelta{nbsp}{closing}omega</p>"
    assert _markdown(document).strip() == f"alpha{nbsp}`beta # gamma delta`{nbsp}omega"
    assert _page_text(document) == f"alpha{nbsp}beta # gamma delta{nbsp}omega"


def test_an_edge_inside_nested_formatting_is_kept_once() -> None:
    nbsp = "\N{NO-BREAK SPACE}"
    written = _markdown(f"<p>alpha<strong><em>{nbsp}beta{nbsp}</em></strong>omega</p>")
    assert written.strip() == f"alpha{nbsp}***beta***{nbsp}omega"
    # An ASCII space beside it, inside the run, is kept with it.
    written = _markdown(f"<p>alpha<strong> {nbsp}beta</strong> omega</p>")
    assert written.strip() == f"alpha {nbsp}**beta** omega"


@pytest.mark.parametrize(
    ("document", "expected"),
    [
        # A heading and a table cell are written as one line each.
        (
            "<h2>alpha<strong>\N{NO-BREAK SPACE}beta</strong></h2>",
            "alpha\N{NO-BREAK SPACE}beta",
        ),
        (
            "<table><tr><th>h</th></tr><tr><td>alpha<em>\N{NO-BREAK SPACE}beta</em></td></tr></table>",
            "alpha\N{NO-BREAK SPACE}beta",
        ),
        (
            "<ul><li>alpha<b>\N{NO-BREAK SPACE}beta</b></li></ul>",
            "alpha\N{NO-BREAK SPACE}beta",
        ),
    ],
)
def test_the_space_is_kept_in_a_heading_a_cell_and_an_item(
    document: str, expected: str
) -> None:
    assert expected in _page_text(document)


# --------------------------------------------------------------------------- #
# The fixtures, as the page WebShot captures
# --------------------------------------------------------------------------- #


def _fixture_page_text(name: str) -> str:
    materialized = materialize_source((FIXTURE_ROOT / name).as_uri())
    try:
        page = file_url_path(materialized.browser_url).read_text("utf-8")
    finally:
        materialized.cleanup()
    body = page.split("<main>", 1)[1]
    return html.unescape(re.sub(r"<[^>]+>", "", body)).replace("~~", "")


@pytest.mark.parametrize(
    "name", ["edge_space_report.docx", "edge_space_book.epub", "edge_space_archive.zip"]
)
def test_each_fixture_line_reads_on_the_page_as_the_document_has_it(name: str) -> None:
    if name.endswith(".docx") and importlib.util.find_spec("mammoth") is None:
        pytest.skip("mammoth is absent — install webshot[office] to run this")
    text = _fixture_page_text(name)
    lines = edge_space_lines(name)
    assert len(lines) > 20, "the fixture lost its lines"
    missing = [line.text for line in lines if line.text not in text]
    assert not missing, missing
