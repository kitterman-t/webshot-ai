"""Code in a line of text stays code, and nothing in it reaches the page (docs/09 P59).

markdownify writes what a link, emphasis, struck text, a subscript, a
quotation or inline code holds into a line of text, after stripping the blank
lines a `<pre>`'s fence stands between.  The fence then began mid-line, where
Python-Markdown's `fenced_code` does not read one, and the page read the code
as inline code only while it stayed one paragraph: a blank line in it ended
the paragraph, a line opening a heading, a rule, a quotation, a link reference,
a definition or a `<div>` split it, and what followed was Markdown.  An `<img>`
typed in the code was an image the capture fetched (the class of docs/09
P28).  Inline code whose own text went over lines did the same, with no `<pre>`
at all.  Every case below was measured live before.

The fixtures are written by `tools/fixtures/office.py`; every word in them is
made up, and `.invalid` never resolves, so nothing here touches a network.
"""

from __future__ import annotations

import html
import importlib.util
import itertools
import random
import re
import sys
from pathlib import Path

import markdown  # type: ignore[import-untyped]
import pytest
from bs4 import BeautifulSoup

from webshot.acquire.adapters import (
    CODE_LINES_CLASS,
    MARKDOWN_EXTENSIONS,
    materialize_source,
    sanitize_markdown_html,
)
from webshot.acquire.markitdown_bridge import BLOCKS, _TextMarkdownify, to_markdown
from webshot.netpolicy import file_url_path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:  # the fixture generator lives under tools/
    sys.path.insert(0, str(REPO_ROOT))

from tools.fixtures import office  # noqa: E402

FIXTURE_ROOT = REPO_ROOT / "tests" / "fixtures" / "office"

TRACKER = "tracker.invalid"

#: The bridge's marks are drawn from Unicode's private use area.
PRIVATE_USE = re.compile("[-]")

#: Every element markdownify writes into a line of text, around `{}`.
INLINE = {
    "a": '<a href="https://example.invalid/l">{}</a>',
    "b": "<b>{}</b>",
    "code": "<code>{}</code>",
    "del": "<del>{}</del>",
    "em": "<em>{}</em>",
    "i": "<i>{}</i>",
    "kbd": "<kbd>{}</kbd>",
    "q": "<q>{}</q>",
    "s": "<s>{}</s>",
    "samp": "<samp>{}</samp>",
    "strike": "<strike>{}</strike>",
    "strong": "<strong>{}</strong>",
    "sub": "<sub>{}</sub>",
    "sup": "<sup>{}</sup>",
    "u": "<u>{}</u>",
}
#: Those that are a code span, which is one line: a line break is a space
#: there, as the browser shows one in inline code.
CODE_SPANS = {"code", "kbd", "samp"}

#: Code that took the page out of the code before, each by its own route.
CODES = {
    "blank-line": f'a\n\n<img src="https://{TRACKER}/blank.png">',
    "line-of-spaces": f'a\n   \n<img src="https://{TRACKER}/spaces.png">',
    "markdown-image": f"a\n\n![m](https://{TRACKER}/markdown.png)",
    "heading": f'a\n# h\n<img src="https://{TRACKER}/heading.png">',
    "rule": f'a\n***\n<img src="https://{TRACKER}/rule.png">',
    "quotation": f'a\n> q\n<img src="https://{TRACKER}/quotation.png">',
    "link-reference": f"a\n[r]: https://{TRACKER}/reference.png\n![m][r]",
    "div": f'a\n<div><img src="https://{TRACKER}/div.png"></div>\nb',
    "definition": f'a\n: d\n<img src="https://{TRACKER}/definition.png">',
    "backtick-runs": f'a ``` b\n````\n\n<img src="https://{TRACKER}/ticks.png">',
    "fence-lines": f'```\n<img src="https://{TRACKER}/fence.png">\n```',
    "tabs-and-indentation": f'\tone\n    two\n\n<img src="https://{TRACKER}/tabs.png">',
    "carriage-returns": f'a\rb\r\n\r\n<img src="https://{TRACKER}/returns.png">',
    "markdown-and-entities": (
        f'&amp; \\* \\_x_ [y](z) <b>no</b> *e*\n\n<img src="https://{TRACKER}/md.png">'
    ),
}

#: Where the element holding the code stands.
CONTEXTS = {
    "alone": "<p>{}</p>",
    "between-text": "<p>lead {} tail</p>",
    "after-a-word": "<p>lead{}tail</p>",
    "item": "<ul><li>{}</li><li>N</li></ul>",
    "item-after-text": "<ol><li>lead {}</li><li>N</li></ol>",
    "nested-item": "<ol><li>A<ul><li>B {}</li></ul></li></ol>",
    "quotation": "<blockquote><p>{}</p></blockquote>",
    "two-deep": "<p><em>x {} y</em></p>",
}


def _converted(tmp_path: Path, body: str) -> str:
    """`body` as the bridge writes an HTML file's Markdown, MarkItDown's own
    clean-up included."""
    source = tmp_path / "page.html"
    # The bytes the case is: Windows writes text with each `\n` a `\r\n`,
    # which is another document where the code holds a `\r` (docs/09 P92).
    source.write_bytes(f"<html><body><p>Top</p>{body}<p>End</p></body></html>".encode())
    return to_markdown(source, suffix=".html").markdown


def _render(source: str) -> str:
    """Markdown as the adapter renders a converted document's."""
    rendered: str = sanitize_markdown_html(
        markdown.markdown(
            source, extensions=list(MARKDOWN_EXTENSIONS), output_format="html5"
        )
    )
    return rendered


def _page(tmp_path: Path, body: str) -> str:
    converted = _converted(tmp_path, body)
    assert not PRIVATE_USE.search(converted), converted
    return _render(converted)


def _assert_inert(page: str) -> None:
    """Nothing the code holds is an element: no image, and the tracker's host
    in no attribute, only in the text of code."""
    soup = BeautifulSoup(page, "html.parser")
    assert soup("img") == [], page
    for element in soup.find_all(True):
        for value in element.attrs.values():
            assert TRACKER not in str(value), page
    for text in soup.find_all(string=lambda text: TRACKER in text):
        assert text.find_parent("code") is not None, page


def _soup(page: str) -> BeautifulSoup:
    """`page`, with the spaces in code kept: bs4 makes a string of spaces
    between two tags one space elsewhere."""
    return BeautifulSoup(
        page, "html.parser", preserve_whitespace_tags=["code", "pre", "textarea"]
    )


def _inline_code(page: str) -> list[str]:
    """The text of each `<code>` outside a code block.  A line break in it is
    a line feed, the text's own, which the page shows in `CODE_LINES_CLASS`;
    there is no `<br>`, which the capture's text surfaces drop."""
    held = []
    for code in _soup(page)("code"):
        if code.find_parent("pre") is not None:
            continue
        text = code.get_text()
        assert code("br") == [], page
        if "\n" in text:
            assert code.get("class") == [CODE_LINES_CLASS], page
        held.append(text)
    return held


def _as_held(code: str, tag: str) -> str:
    """`code` as the page holds it in `tag`: line for line, tabs expanded as
    at the top level; in a code span, its words on one line."""
    lines = re.split(r"\r\n?|\n", code)
    if tag in CODE_SPANS:
        return " ".join(" ".join(lines).split())
    return "\n".join(line.expandtabs(4) for line in lines)


@pytest.mark.parametrize(("tag", "context"), list(itertools.product(INLINE, CONTEXTS)))
def test_code_in_a_line_of_text_is_code_and_nothing_else(
    tmp_path: Path, tag: str, context: str
) -> None:
    """Measured before, for each element in each place: an `<img>` reached the
    page through the blank-line code everywhere but in a quotation mark and a
    subscript at the top level, which write their code on lines of its own;
    there and in an item, through a heading's, a rule's or a `<div>`'s."""
    body = "".join(
        CONTEXTS[context].format(INLINE[tag].format(f"<pre>{html.escape(code)}</pre>"))
        for code in CODES.values()
    )
    page = _page(tmp_path, body)
    _assert_inert(page)
    held = [
        " ".join(text.split()) if tag in CODE_SPANS else text
        for text in _inline_code(page)
    ]
    assert held == [_as_held(code, tag) for code in CODES.values()], page


@pytest.mark.parametrize("tag", sorted(INLINE.keys() - CODE_SPANS))
def test_code_with_text_beside_it_in_the_element_is_the_elements(
    tmp_path: Path, tag: str
) -> None:
    """Before, the fence began a line after the text, and ended before the
    element's closing markup, so `fenced_code` read none of it."""
    pre = f"<pre>{html.escape(CODES['blank-line'])}</pre>"
    page = _page(tmp_path, "<p>" + INLINE[tag].format(f"before {pre} after") + "</p>")
    _assert_inert(page)
    assert _inline_code(page) == [CODES["blank-line"]]
    # A space either side, where the block's own lines kept them apart.
    assert re.search(r"before a\n\n<img[^>]*> after", _soup(page).get_text()), page


@pytest.mark.parametrize("tag", sorted(CODE_SPANS))
@pytest.mark.parametrize("code", list(CODES))
def test_inline_code_over_lines_is_one_line(
    tmp_path: Path, tag: str, code: str
) -> None:
    """Inline code's own text, no `<pre>`: markdownify kept its line breaks in
    the code span, and a line opening a heading, a rule, a quotation or a
    `<div>` split the paragraph it was in."""
    body = f"<p>{INLINE[tag].format(html.escape(CODES[code]))}</p>"
    page = _page(tmp_path, body)
    _assert_inert(page)
    assert [" ".join(text.split()) for text in _inline_code(page)] == [
        " ".join(CODES[code].split())
    ]


def test_a_paragraph_in_inline_code_is_its_text(tmp_path: Path) -> None:
    """markdownify writes a paragraph on lines of its own, and in a code span
    that was a blank line: the image after it was on the page."""
    body = f'<p><code>a<p>&lt;img src="https://{TRACKER}/p.png"&gt;</p></code></p>'
    page = _page(tmp_path, body)
    _assert_inert(page)
    assert _inline_code(page) == [f'a  <img src="https://{TRACKER}/p.png">']


def test_code_in_a_link_keeps_its_lines_where_it_was_one_line(tmp_path: Path) -> None:
    """With no blank line, the page held the link's code as inline code with
    its lines run together; it holds it in the link still, line for line."""
    code = "first line\n    indented\nlast"
    page = _page(
        tmp_path, f'<p><a href="https://example.invalid/l"><pre>{code}</pre></a></p>'
    )
    element = BeautifulSoup(page, "html.parser").find("code")
    assert element is not None and element.parent is not None, page
    assert element.parent.name == "a", page
    assert _inline_code(page) == [code]


def test_code_in_inline_code_is_its_text_without_a_fence(tmp_path: Path) -> None:
    """The fence was the code span's text: the page showed its backticks."""
    page = _page(tmp_path, "<p><code><pre>a\nb</pre></code></p>")
    assert _inline_code(page) == ["a b"]


def test_code_in_code_in_a_line_of_text_is_the_outer_codes(tmp_path: Path) -> None:
    """The inner `<pre>` is text of the outer one's, whose fence it cannot
    close; the outer one is the link's inline code."""
    inner = html.escape(CODES["blank-line"])
    page = _page(tmp_path, f"<p><a href='x'><pre>x\n<pre>{inner}</pre></pre></a></p>")
    _assert_inert(page)
    (held,) = _inline_code(page)
    assert held.startswith("x\n") and "\n```\na\n\n<img" in held, held


@pytest.mark.parametrize(
    "body",
    [
        "<p><a href='x'><blockquote>{}</blockquote></a></p>",
        "<p><em><ul><li>w {}</li></ul></em></p>",
        "<p><strong><dl><dt>t</dt><dd>{}</dd></dl></strong></p>",
        "<p><del>lead <blockquote><blockquote>{}</blockquote></blockquote></del></p>",
    ],
)
def test_code_in_a_block_in_a_line_of_text_is_the_lines(
    tmp_path: Path, body: str
) -> None:
    """A quotation, an item or a definition written into a line of text is
    malformed by it, and so was an indented code block in one: the link's
    `](x)` or the emphasis's `*` closed the code's last line, and the opener
    stood alone before it.  It is the line's inline code, as anywhere in one."""
    code = CODES["blank-line"]
    page = _page(tmp_path, body.format(f"<pre>{html.escape(code)}</pre>"))
    _assert_inert(page)
    assert BeautifulSoup(page, "html.parser")("pre") == [], page
    assert _inline_code(page) == [code]


# --------------------------------------------------------------------------- #
# What already worked, and must still
# --------------------------------------------------------------------------- #

#: Elements markdownify writes on lines of their own, each around `{}`, and
#: whether a `<pre>` there is a fenced block or, in a line markdownify flattens,
#: the code span it was.
HOLDERS = {
    "top-level": "{}",
    "article": "<article>{}</article>",
    "blockquote": "<blockquote>{}</blockquote>",
    "caption": "<table><caption>{}</caption><tr><td>c</td></tr></table>",
    "dd": "<dl><dt>t</dt><dd>{}</dd></dl>",
    "div": "<div>{}</div>",
    "dt": "<dl><dt>{}</dt><dd>d</dd></dl>",
    "figcaption": "<figure><figcaption>{}</figcaption></figure>",
    "figure": "<figure>{}</figure>",
    "heading": "<h2>{}</h2>",
    "item": "<ul><li>A{}</li></ul>",
    "p": "<p>{}</p>",
    "section": "<section>{}</section>",
    "span": "<span>{}</span>",
    "table-cell": "<table><tr><th>h</th></tr><tr><td>{}</td></tr></table>",
}


@pytest.mark.parametrize("holder", list(HOLDERS))
def test_code_outside_a_line_of_text_is_written_as_before(
    tmp_path: Path, holder: str
) -> None:
    """A fence, an item's, a quotation's or a definition's indented block, or
    a heading's or a cell's code span, as markdownify, P44 and P47 write them;
    none of this change's `<code>`."""
    body = HOLDERS[holder].format("<pre>a\nb</pre>")
    converted = _converted(tmp_path, body)
    assert "<code" not in converted, converted
    if holder in ("item", "dd"):
        assert "\n        a\n        b" in converted, converted
        assert "```" not in converted, converted
    elif holder == "blockquote":
        assert "\n>     a\n>     b" in converted, converted
        assert "```" not in converted, converted
    else:
        assert "```" in converted, converted


def test_every_converter_is_a_block_a_line_or_named() -> None:
    """Each element markdownify converts writes on lines of its own
    (`BLOCKS`) or into a line of text; one this does not name is taken to
    write into a line, which is safe anywhere.  A release that adds one fails
    here, so that which it does is decided."""
    converters = {
        name.removeprefix("convert_")
        for name in dir(_TextMarkdownify)
        if name.startswith("convert_") and callable(getattr(_TextMarkdownify, name))
    }
    others = {
        # `_inline` for what they hold: one line, which markdownify flattens.
        "hN": "every heading",
        "hn": "MarkItDown's heading writer, named for markdownify's old one",
        "td": "a table cell",
        "th": "a table cell",
        # `_noformat` for what it holds: inside a fence.
        "pre": "a code block",
        # Hold nothing markdownify writes.
        "br": "empty",
        "hr": "empty",
        "img": "empty",
        "input": "empty",
        # Writes what it holds as a link's or an image's text: into a line,
        # as anything this module does not name is taken to.
        "video": "a link to its source, or its poster",
        "script": "removed before markdownify runs",
        "style": "removed before markdownify runs",
        "list": "`ol`'s and `ul`'s",
        "soup": "markdownify's entry point",
    }
    blocks = {name.strip("[]").replace("document", "_document_") for name in BLOCKS}
    assert converters == blocks | INLINE.keys() | others.keys()


# --------------------------------------------------------------------------- #
# Any nesting
# --------------------------------------------------------------------------- #

#: How many seeded trees `test_code_in_any_nesting_of_lines_of_text` draws.
TREES = 40


@pytest.mark.parametrize("seed", range(TREES))
def test_code_in_any_nesting_of_lines_of_text(tmp_path: Path, seed: int) -> None:
    """Up to four elements around the code, drawn from those that write into
    a line and those that write on lines of their own, at least one of the
    first: a link around a list, emphasis in a quotation, a `<div>` in struck
    text in an item.  Nothing in the code reaches the page as an element, no
    mark is left, and the code's words are all in code.  A block written into
    a line is malformed by it, as it was: emphasis around a list runs its
    marker into the `**`, and `<u>` in `<samp>` is the span's text."""
    rng = random.Random(seed)
    around = {
        **INLINE,
        "div": "<div>{}</div>",
        "p": "<p>{}</p>",
        "li": "<ul><li>w {}</li></ul>",
        "blockquote": "<blockquote>{}</blockquote>",
        "span": "<span>{}</span>",
    }
    body = ""
    expected_words: list[str] = []
    for code in CODES.values():
        chain = [rng.choice(list(INLINE))] + [
            rng.choice(list(around)) for _ in range(rng.randint(0, 3))
        ]
        rng.shuffle(chain)
        element = f"<pre>{html.escape(code)}</pre>"
        for tag in chain:
            element = around[tag].format(element)
        body += f"<div>{element}</div>"
        expected_words += code.split()
    page = _page(tmp_path, body)
    _assert_inert(page)
    held = " ".join(code.get_text() for code in _soup(page)("code"))
    for word in expected_words:
        assert word in held, (word, body)


# --------------------------------------------------------------------------- #
# Converted documents
# --------------------------------------------------------------------------- #


def _fixture_page(name: str) -> tuple[str, str]:
    """The page WebShot captures for one fixture: its head, and its body."""
    materialized = materialize_source((FIXTURE_ROOT / name).as_uri())
    try:
        page = file_url_path(materialized.browser_url).read_text("utf-8")
    finally:
        materialized.cleanup()
    head, body = page.split("<main>", 1)
    return head, body


@pytest.mark.parametrize(
    "name",
    ["inline_code_book.epub", "inline_code_report.docx", "inline_code_archive.zip"],
)
def test_code_in_a_line_of_text_in_a_document_is_code(name: str) -> None:
    """An EPUB chapter, a Word file whose style map writes code into strong
    text and emphasis, and an HTML page and that Word file in a zip.  Measured
    before: ten images on these pages, and the book's sections after its
    quotation were raw HTML."""
    if name != "inline_code_book.epub" and importlib.util.find_spec("mammoth") is None:
        pytest.skip("mammoth is absent — install webshot[office] to run this")
    stem = name.split(".")[0].upper()
    expected: list[tuple[str, str]] = getattr(office, f"{stem}_CODE")
    head, page = _fixture_page(name)
    # The page shows the code's own line breaks, as it does a `<pre>`'s.
    assert re.search(
        rf"code\.{CODE_LINES_CLASS} {{ white-space: pre-wrap; tab-size: 4; }}", head
    ), head
    _assert_inert(page)
    _inline_code(page)
    soup = _soup(page)
    held = [
        (code.parent.name, code.get_text())
        for code in soup("code")
        if code.parent is not None
        and code.find_parent("pre") is None
        and code.get_text() != name
    ]
    assert held == expected
    blocks = [pre.get_text().removesuffix("\n") for pre in soup("pre")]
    assert blocks == getattr(office, f"{stem}_BLOCKS", [])
