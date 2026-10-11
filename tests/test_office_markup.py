"""A document's text reaches the page as text (docs/09 P28).

MarkItDown writes what a document says into Markdown, and the page is rendered
from that Markdown, which passes raw HTML through and reads `#`, `*`, `[`, `3.`
and the rest as structure.  So `<img src="https://…">` typed on a slide, in a
Word paragraph, a spreadsheet cell, an EPUB chapter or a text file inside a zip
became an image the capture fetched.  These tests hold the bridge's escaping
to that: every `markup_*` fixture names `.invalid` images in every place its
reader takes text from, and none of them may become an element.

The fixtures are written by `tools/fixtures/office.py`; every word in them is
made up, and `.invalid` never resolves, so nothing here touches a network.
"""

from __future__ import annotations

import html
import importlib.util
import io
import re
import sys
import zipfile
from pathlib import Path

import markdown  # type: ignore[import-untyped]
import pytest
from markitdown import StreamInfo
from markitdown.converters import (
    DocxConverter,
    EpubConverter,
    HtmlConverter,
    PptxConverter,
    XlsxConverter,
    ZipConverter,
)

from webshot.acquire.adapters import (
    MARKDOWN_EXTENSIONS,
    materialize_source,
    sanitize_markdown_html,
)
from webshot.acquire.markitdown_bridge import (
    INLINE_MARKDOWN,
    LINE_START_MARKDOWN,
    _as_text_lines,
    _escape_cell,
    _markitdown,
    _TextHtmlConverter,
    to_markdown,
)
from webshot.errors import WebShotError
from webshot.netpolicy import file_url_path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:  # the fixture generator lives under tools/
    sys.path.insert(0, str(REPO_ROOT))

from tools.fixtures.office import (  # noqa: E402
    BEACON,
    BREAKOUT_HREF,
    BREAKOUT_PRE,
    MARKUP_SHEET,
    SENTINELS,
    markup_lines,
)

FIXTURE_ROOT = REPO_ROOT / "tests" / "fixtures" / "office"


def _render(source: str) -> str:
    """Markdown as the adapter renders a converted document's."""
    rendered: str = sanitize_markdown_html(
        markdown.markdown(
            source, extensions=list(MARKDOWN_EXTENSIONS), output_format="html5"
        )
    )
    return rendered


def _page(name: str) -> str:
    """The body of the page WebShot captures for one fixture."""
    materialized = materialize_source((FIXTURE_ROOT / name).as_uri())
    try:
        page = file_url_path(materialized.browser_url).read_text("utf-8")
    finally:
        materialized.cleanup()
    return page.split("<main>", 1)[1]


def _text(body: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", body)))


def _requires(module: str) -> None:
    if importlib.util.find_spec(module) is None:
        pytest.skip(f"{module} is absent — install webshot[office] to run this")


# --------------------------------------------------------------------------- #
# The encoding itself
# --------------------------------------------------------------------------- #


def test_every_character_markdown_escapes_is_encoded_or_needs_no_encoding() -> None:
    """The encoded set is the renderer's own grammar, so the two cannot drift.

    Python-Markdown lists the characters a backslash escapes, which is the set
    its configured extensions give a meaning.  Each is encoded wherever it
    stands or wherever a line begins, or is listed here with why it needs
    neither: an extension that adds one must be met by a decision.
    """
    renderer = markdown.Markdown(extensions=list(MARKDOWN_EXTENSIONS))
    not_encoded = {
        ">": "written as &gt; with HTML's characters",
        ".": "a list only after digits at a line start, which is encoded",
        "(": "a link destination only after `]`, which is encoded",
        ")": "a link destination only after `]`, which is encoded",
    }
    escaped = set(renderer.ESCAPED_CHARS)
    encoded = set(INLINE_MARKDOWN) | set(LINE_START_MARKDOWN)
    assert escaped - encoded == set(not_encoded)


@pytest.mark.parametrize(
    "text",
    [
        '<img src="https://tracker.invalid/x.png"> and *stars*',
        "# Not a heading",
        "C#",
        "[link](https://example.invalid) ![image](https://tracker.invalid/i.png)",
        "[id]: https://example.invalid/reference",
        "*[HTML]: an abbreviation",
        "3. Third, as typed",
        "Total\n===",
        "Total\n---",
        "Term\n: not a definition",
        "~~~\nnot a fence",
        "```\nnot a fence",
        "a | b\n--- | ---\n1 | 2",
        "Set {x}",
        "Heading {#id}",
        "C:\\Users\\_x_ and a\\*b and __init__",
        "&copy; as typed",
        "robots!",
        "x\n\n\tindented, not code",
        "x\n\n    indented, not code",
        "- not a bullet\n+ nor this",
        "$\\frac{a}{b} \\le x_{1}^{*}$",
    ],
)
def test_text_renders_as_itself(text: str) -> None:
    rendered = _render(_as_text_lines(text))
    assert re.sub(r"</?p>", "", rendered).count("<") == 0, rendered
    expected = [line.strip() for line in text.split("\n") if line.strip()]
    actual = [
        line.strip()
        for line in html.unescape(rendered)
        .replace("<p>", "\n")
        .replace("</p>", "\n")
        .split("\n")
        if line.strip()
    ]
    assert actual == expected


def test_text_that_needed_no_encoding_renders_byte_for_byte_as_it_did() -> None:
    """nh3 writes a reference back as its character, anchors included."""
    for text in ("Field notes, 3.14 (well-known) a: b = c ~ d + e", "Q&A: 2026"):
        assert _render(_as_text_lines(text)) == _render(text)
        assert _render(f"# {_as_text_lines(text)}") == _render(f"# {text}")


@pytest.mark.parametrize(
    "cell",
    [
        "=SUM(A1)",
        "~approx",
        "Set {x}",
        "C:\\Users\\_x_",
        "a\\*b",
        "x | y",
        "back\\| slash",
        '<img src="https://tracker.invalid/cell.png">',
        "[a](https://example.invalid)",
        "-3.5",
    ],
)
def test_a_cell_renders_as_itself(cell: str) -> None:
    """The first CSV fix put a backslash before `=` and `~`, which
    Python-Markdown prints, dropped a trailing `{x}` as attributes, and let a
    cell's own backslash escape what followed it (docs/09 P28)."""
    rendered = _render(f"| h |\n| --- |\n| {_escape_cell(cell)} |")
    assert f"<td>{html.escape(cell, quote=False)}</td>" in rendered, rendered


# --------------------------------------------------------------------------- #
# Each format, from its fixture
# --------------------------------------------------------------------------- #


def test_a_decks_text_is_text_wherever_python_pptx_reads_it() -> None:
    _requires("pptx")
    body = _page("markup_deck.pptx")
    assert not re.findall(r"<img\b|<a\b|<em\b|<strong\b|<dl\b|<ol\b|<pre\b", body)
    text = _text(body)
    for line in [SENTINELS["markup_deck.pptx"], *markup_lines("slide")]:
        assert line in text, line
    assert "\tIndented, not code".strip() in text
    # The slide's title is the page's one heading of the document's own...
    assert re.findall(r"<h1\b[^>]*>(.*?)</h1>", body) == [
        html.escape(f'<img src="{BEACON}/title.png"> and *title*', quote=False)
    ]
    # ...and a table cell, a chart's title, category and series, and the notes
    # are each text: MarkItDown writes all of them straight into Markdown.
    for string in (
        f'<img src="{BEACON}/cell.png">',
        "# cell",
        "Chart: [chart](https://example.invalid/chart) _x_",
        f'<img src="{BEACON}/category.png">',
        "<b>series</b>",
        f'<img src="{BEACON}/notes.png"> *notes*',
    ):
        assert string in text, string


def test_a_word_files_text_alt_text_and_link_target_are_text() -> None:
    _requires("mammoth")
    body = _page("markup_report.docx")
    assert not re.findall(r"<em\b|<strong\b|<dl\b|<ol\b|<pre\b|<h[2-6]\b", body)
    text = _text(body)
    for line in [SENTINELS["markup_report.docx"], *markup_lines("word")]:
        assert line in text, line
    # The picture's alt text stays its attribute; mammoth's data URI is the
    # only image there is.
    images = re.findall(r"<img\b[^>]*>", body)
    assert len(images) == 1 and 'src="data:image/png;base64..."' in images[0]
    assert 'alt="a chart](https://tracker.invalid/alt.png) ![x"' in images[0]
    # The hyperlink is one link, to its own target, `(` and `)` encoded.
    links = re.findall(r'<a\b[^>]*href="([^"]*)"[^>]*>(.*?)</a>', body)
    assert links == [
        (BREAKOUT_HREF.replace("(", "%28").replace(")", "%29"), "a link out")
    ]


def test_a_workbooks_cells_and_sheet_name_are_text() -> None:
    _requires("openpyxl")
    body = _page("markup_workbook.xlsx")
    assert not re.findall(r"<img\b|<a\b|<em\b|<strong\b", body)
    text = _text(body)
    for line in [SENTINELS["markup_workbook.xlsx"], *markup_lines("cell")]:
        assert line.replace("\n", " ") in text, line
    assert "=SUM(A1)" in text and "\\=" not in text
    # MarkItDown writes the sheet's name as a heading of its own.
    headings = re.findall(r"<h2\b[^>]*>(.*?)</h2>", body)
    assert [html.unescape(heading) for heading in headings] == [MARKUP_SHEET]


def test_an_epubs_chapters_metadata_and_code_are_text() -> None:
    body = _page("markup_book.epub")
    assert not re.findall(r"<em\b|<dl\b|<ol\b", body)
    text = _text(body)
    for line in [SENTINELS["markup_book.epub"], *markup_lines("chapter")]:
        assert line in text, line
    # Metadata MarkItDown writes itself, after its own `**Title:**` labels.
    assert f"<b>Markup</b> *book* & {{{SENTINELS['markup_book.epub']}}}" in text
    assert "# Creator" in text
    assert f'<img src="{BEACON}/description.png">' in text
    # A sentence ending in `!` before a link: the link, not an image.
    assert '<a href="#note" rel="noopener noreferrer">1</a>' in body
    # One code block, which its own fence line did not close.
    blocks = re.findall(r"<pre><code>(.*?)</code></pre>", body, re.DOTALL)
    assert [html.unescape(block).strip() for block in blocks] == [BREAKOUT_PRE]
    images = re.findall(r"<img\b[^>]*>", body)
    assert images == [
        '<img alt="a chart](https://tracker.invalid/alt.png) ![x"'
        ' src="data:image/png;base64...">'
    ]


def test_an_epubs_title_is_given_back_as_the_book_has_it() -> None:
    """The bridge encodes the metadata it reads and decodes the title it returns:
    the adapter escapes that title itself, for the page's `<title>`."""
    converted = to_markdown(FIXTURE_ROOT / "markup_book.epub", suffix=".epub")
    assert converted.title == (
        f"<b>Markup</b> *book* & {{{SENTINELS['markup_book.epub']}}}"
    )


def test_an_archives_members_and_their_names_are_text() -> None:
    body = _page("markup_archive.zip")
    assert not re.findall(r"<img\b|<a\b|<em\b|<strong\b|<dl\b|<ol\b", body)
    headings = [
        html.unescape(heading) for heading in re.findall(r"<h2\b[^>]*>(.*?)</h2>", body)
    ]
    assert headings == [
        "File: notes/markup.txt",
        "File: data/markup.csv",
        "File: data/markup.tsv",
        "File: notes/<img src=https:tracker.invalid>.txt",
    ]
    # A text member is a code block with its lines, as a top-level `.txt` is.
    blocks = re.findall(r"<pre><code>(.*?)</code></pre>", body, re.DOTALL)
    lines = [SENTINELS["markup_archive.zip"], *markup_lines("member")]
    assert html.unescape(blocks[0]).strip().split("\n") == lines
    # The CSV inside is escaped as a top-level one is, and the TSV is a table.
    cells = [html.unescape(cell) for cell in re.findall(r"<td>(.*?)</td>", body)]
    assert cells[:5] == [
        f'<img src="{BEACON}/csv.png">',
        "=SUM(A1)",
        "~approx",
        "Set {x}",
        "C:\\Users\\_x_",
    ]
    assert cells[5:] == [f'<img src="{BEACON}/tsv.png">', "#"]


# --------------------------------------------------------------------------- #
# The converters that do it
# --------------------------------------------------------------------------- #


def test_no_converter_that_writes_text_as_markdown_is_left_registered() -> None:
    """MarkItDown moves on to the next converter when one raises, so a builtin
    left beside its replacement would read what the replacement refused."""
    replaced = (
        DocxConverter,
        EpubConverter,
        HtmlConverter,
        PptxConverter,
        XlsxConverter,
        ZipConverter,
    )
    kinds = [type(registration.converter) for registration in _markitdown()._converters]
    assert not [kind for kind in kinds if kind in replaced]


def test_a_deck_the_rewrite_refuses_is_not_read_raw_instead(tmp_path: Path) -> None:
    """A notes part declaring an entity is refused (spec §6.6), and the run
    ends there: one attempt, by the replacement, and no builtin left to read
    the same deck with its text unescaped."""
    _requires("pptx")
    hostile = tmp_path / "refused.pptx"
    with (
        zipfile.ZipFile(FIXTURE_ROOT / "markup_deck.pptx") as original,
        zipfile.ZipFile(hostile, "w") as target,
    ):
        for info in original.infolist():
            payload = original.read(info)
            if info.filename.startswith("ppt/notesSlides/notesSlide"):
                declaration = b'<!DOCTYPE x [<!ENTITY e "e">]>'
                payload = payload.replace(b"?>", b"?>" + declaration, 1)
            target.writestr(info, payload)
    with pytest.raises(WebShotError) as refused:
        materialize_source(hostile.as_uri())
    message = str(refused.value)
    assert "after 1 attempts" in message, message
    assert "_TextPptxConverter threw ValueError" in message, message
    assert "refuses to parse (EntitiesForbidden" in message, message


@pytest.mark.parametrize(
    "document",
    [
        "<h1>Report</h1><p>Plain words, then <em>emphasis</em> and <strong>strength"
        "</strong>.</p><ul><li>one</li><li>two</li></ul><ol><li>first</li></ol>",
        '<p>See <a href="https://example.invalid/page" title="Page">the page</a> and'
        ' <img src="figure.png" alt="A figure"> with <code>inline code</code>.</p>',
        "<table><tr><th>Region</th><th>Revenue</th></tr><tr><td>North</td>"
        "<td>1250000</td></tr></table><blockquote><p>Quoted words</p></blockquote>",
        "<pre>def run():\n    return 1\n</pre><h2>Next</h2><p>After the code</p>",
    ],
)
def test_the_text_html_converter_is_upstreams_on_plain_text(document: str) -> None:
    """`_TextHtmlConverter.convert` copies MarkItDown's; on a document whose
    text needs no escaping the two must render the same page."""
    page = f"<html><body>{document}</body></html>"
    ours = _TextHtmlConverter().convert_string(page).markdown
    upstream = HtmlConverter().convert_string(page).markdown
    assert _render(ours) == _render(upstream)


def test_the_too_deep_fallback_writes_text_as_text() -> None:
    """Upstream returns the plain text raw when markdownify recurses too far."""
    depth = sys.getrecursionlimit() + 100
    page = (
        "<html><body>"
        + "<div>" * depth
        + '<p>&lt;img src="https://tracker.invalid/deep.png"&gt;</p>'
        + "</div>" * depth
        + "</body></html>"
    )
    with pytest.warns(UserWarning, match="too deeply nested"):
        result = _TextHtmlConverter().convert(
            io.BytesIO(page.encode()),
            stream_info=StreamInfo(
                mimetype="text/html", extension=".html", charset="utf-8"
            ),
        )
    assert "<img" not in _render(result.markdown)
