"""A `<pre>` in a converted document's quotation or definition keeps its lines
(docs/09 P47).

markdownify fences a `<pre>`, a quotation puts `> ` before each line of it and
a definition `:   ` or four spaces, and Python-Markdown's `fenced_code` reads a
fence only where a line begins.  So the code was read as inline code in a
paragraph, the browser ran its lines together, and a blank line in it ended
that paragraph: what followed was Markdown, raw HTML included.  The writer now
makes it an indented code block in its container.  Each case compares the
`<pre>` on the page with the same `<pre>` written at the top level, whose
Markdown is unchanged.  Every case below was measured on the page before and
after.
"""

from __future__ import annotations

import html
import itertools
import random
import re
from pathlib import Path

import markdown  # type: ignore[import-untyped]
import pytest
from bs4 import BeautifulSoup, Tag

from webshot.acquire.adapters import (
    CODE_LINES_CLASS,
    MARKDOWN_EXTENSIONS,
    sanitize_markdown_html,
)
from webshot.acquire.markitdown_bridge import to_markdown

#: The bridge's marks are drawn from Unicode's private use area.
PRIVATE_USE = re.compile("[\ue000-\uf8ff]")

#: What a code block can sit in on the page, and what keeps it apart there.
STRUCTURE = ("blockquote", "dl", "dt", "dd", "ol", "ul", "li")


def _converted(tmp_path: Path, body: str) -> str:
    """`body` as the bridge writes an HTML file's Markdown, MarkItDown's own
    clean-up included: it strips each line's end and runs of blank lines."""
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
    page = _render(converted)
    assert "<!--" not in page, page
    return page


def _code(page: str) -> list[str]:
    return [pre.get_text() for pre in BeautifulSoup(page, "html.parser")("pre")]


def _top_level(tmp_path: Path, code: str) -> str:
    """`code` as the page holds a `<pre>` of it outside any container."""
    (block,) = _code(_page(tmp_path, f"<pre>{html.escape(code)}</pre>"))
    return block


def _holders(pre: Tag) -> str:
    """The quotations, definitions and items around `pre`, outermost first."""
    return "/".join(
        holder.name for holder in reversed(pre.find_parents(["blockquote", "dd", "li"]))
    )


def _where(page: str) -> list[str]:
    return [_holders(pre) for pre in BeautifulSoup(page, "html.parser")("pre")]


def _structure(page: str) -> list[str]:
    """Each quotation, definition list and list on `page`, and each of their
    parts, as the path of containers that leads to it."""
    soup = BeautifulSoup(page, "html.parser")
    return [
        "/".join(
            [
                parent.name
                for parent in reversed(list(element.parents))
                if parent.name in STRUCTURE
            ]
            + [element.name]
        )
        for element in soup.find_all(STRUCTURE)
    ]


# --------------------------------------------------------------------------- #
# Where the code sits
# --------------------------------------------------------------------------- #

CODE = "code(1)\n  indented"

#: Each shape, its `<pre>` written as `{}`, and the containers it sits in.
SHAPES = {
    "quotation": ("<blockquote>{}</blockquote>", "blockquote"),
    "quotation-between-paragraphs": (
        "<blockquote><p>q</p>{}<p>r</p></blockquote>",
        "blockquote",
    ),
    "quotation-after-text": ("<blockquote>q{}r</blockquote>", "blockquote"),
    "quotation-in-a-div": ("<blockquote><div>{}</div></blockquote>", "blockquote"),
    "quotation-after-a-list": (
        "<blockquote><ul><li>x</li></ul>{}</blockquote>",
        "blockquote",
    ),
    "quotation-after-a-definition-list": (
        "<blockquote><dl><dt>t</dt><dd>d</dd></dl>{}</blockquote>",
        "blockquote",
    ),
    "quotation-in-a-quotation": (
        "<blockquote><p>a</p><blockquote>{}</blockquote><p>b</p></blockquote>",
        "blockquote/blockquote",
    ),
    "three-quotations-deep": (
        "<blockquote><blockquote><blockquote><p>c</p>{}</blockquote></blockquote>"
        "</blockquote>",
        "blockquote/blockquote/blockquote",
    ),
    "quotation-in-an-item": (
        "<ul><li>A<blockquote><p>q</p>{}</blockquote></li><li>N</li></ul>",
        "li/blockquote",
    ),
    "quotation-opening-an-item": (
        "<ol><li><blockquote>{}</blockquote></li><li>N</li></ol>",
        "li/blockquote",
    ),
    "item-in-a-quotation": (
        "<blockquote><ul><li>A{}</li><li>N</li></ul></blockquote>",
        "blockquote/li",
    ),
    "definition": ("<dl><dt>t</dt><dd>{}</dd><dt>u</dt><dd>x</dd></dl>", "dd"),
    "definition-after-text": ("<dl><dt>t</dt><dd>d{}e</dd></dl>", "dd"),
    "definition-between-paragraphs": (
        "<dl><dt>t</dt><dd><p>d</p>{}<p>e</p></dd></dl>",
        "dd",
    ),
    "definition-after-a-list": (
        "<dl><dt>t</dt><dd><ul><li>x</li></ul>{}</dd></dl>",
        "dd",
    ),
    "definition-in-a-definition": (
        "<dl><dt>t</dt><dd><dl><dt>u</dt><dd>{}</dd></dl></dd></dl>",
        "dd/dd",
    ),
    "definition-in-a-quotation": (
        "<blockquote><dl><dt>t</dt><dd>{}</dd></dl></blockquote>",
        "blockquote/dd",
    ),
    "quotation-in-a-definition": (
        "<dl><dt>t</dt><dd><blockquote>{}</blockquote></dd></dl>",
        "dd/blockquote",
    ),
    "item-in-a-definition": (
        "<dl><dt>t</dt><dd><ul><li>A{}</li></ul></dd></dl>",
        "dd/li",
    ),
    "quotation-in-a-definition-in-an-item": (
        "<ul><li>A<dl><dt>t</dt><dd><blockquote>{}</blockquote></dd></dl></li></ul>",
        "li/dd/blockquote",
    ),
}


@pytest.mark.parametrize("shape", SHAPES)
def test_code_in_a_quotation_or_a_definition_keeps_its_lines(
    tmp_path: Path, shape: str
) -> None:
    """Measured before, in every shape but the two items P44 already wrote:
    no `<pre>` on the page; the code was `<code>code(1) indented</code>` in a
    paragraph of its container."""
    wrapper, where = SHAPES[shape]
    body = wrapper.format(f"<pre>{CODE}</pre>")
    page = _page(tmp_path, body)
    assert _code(page) == [_top_level(tmp_path, CODE)], page
    assert _where(page) == [where], page
    plain = _page(tmp_path, wrapper.format("<p>codeword</p>"))
    assert _structure(page) == _structure(plain), page


def test_two_blocks_of_code_in_a_quotation_or_a_definition_stay_two(
    tmp_path: Path,
) -> None:
    """Python-Markdown joins indented code blocks with only blank lines, or
    only `>` lines, between them."""
    for wrapper in ("<blockquote>{}</blockquote>", "<dl><dt>t</dt><dd>{}</dd></dl>"):
        page = _page(tmp_path, wrapper.format("<pre>one</pre><pre>two</pre>"))
        assert _code(page) == ["one\n", "two\n"], page


@pytest.mark.parametrize(
    ("body", "where"),
    [
        ("<blockquote><ul><li>x</li></ul><pre>code</pre></blockquote>", "blockquote"),
        (
            "<blockquote><dl><dt>t</dt><dd>x</dd></dl><pre>code</pre></blockquote>",
            "blockquote",
        ),
        ("<dl><dt>t</dt><dd><ul><li>x</li></ul><pre>code</pre></dd></dl>", "dd"),
    ],
)
def test_code_after_a_list_is_its_containers_not_the_lists(
    tmp_path: Path, body: str, where: str
) -> None:
    """Python-Markdown places an indented block by its depth alone: after a
    list, or a definition list, it was more of the last item or definition,
    or with no blank line between, of its text."""
    assert _where(_page(tmp_path, body)) == [where]


def test_a_definition_that_opens_with_code_holds_it(tmp_path: Path) -> None:
    """A definition's text cannot be a code block on its `:` line, so it is a
    comment there, which nh3 takes off the page."""
    page = _page(tmp_path, f"<dl><dt>t</dt><dd><pre>{CODE}</pre></dd></dl>")
    assert re.search(r"<dd>\s*<pre><code>", page), page


# --------------------------------------------------------------------------- #
# What the code holds
# --------------------------------------------------------------------------- #

#: Code that a fence, an indentation or Markdown could take for something else.
CODES = {
    "blank-line": "one\n\ntwo",
    "leading-and-trailing-whitespace": "\n\n   lead\nmid  \n\n  ",
    "tabs": "\tone\nab\ttwo",
    "three-backticks": "```\nfenced\n```",
    "longer-backtick-runs": "````\n`````\n``x`` and `y`",
    "carriage-returns": "a\rb\r\nc",
    "markup": (
        "<div>\n<!-- open\n<script>x</script>\n</blockquote></dd>\n&amp; &lt;\n"
        "* item\n1. number\n> quoted\n# heading\n[^1]: note\n*[HTML]: abbr\n"
        ": definition\n| a | b |\n|---|---|\n---\n~~~\n{: #anchor}"
    ),
}

WRAPPERS = {
    "quotation": "<blockquote><p>q</p>{}</blockquote>",
    "three-quotations-deep": "<blockquote><blockquote><blockquote>{}</blockquote></blockquote></blockquote>",
    "definition": "<dl><dt>t</dt><dd>d{}</dd></dl>",
    "definition-in-a-quotation": "<blockquote><dl><dt>t</dt><dd>{}</dd></dl></blockquote>",
}


@pytest.mark.parametrize(("name", "wrapper"), list(itertools.product(CODES, WRAPPERS)))
def test_code_holds_what_code_at_the_top_level_holds(
    tmp_path: Path, name: str, wrapper: str
) -> None:
    """Each line as the top level keeps it: markdownify trims the blank lines
    at either end, MarkItDown each line's trailing spaces, and Python-Markdown
    expands tabs; an indented block needs no fence, so backticks are text."""
    code = CODES[name]
    page = _page(tmp_path, WRAPPERS[wrapper].format(f"<pre>{html.escape(code)}</pre>"))
    assert _code(page) == [_top_level(tmp_path, code)], page


@pytest.mark.parametrize(
    "wrapper", ["<blockquote>{}</blockquote>", "<dl><dt>t</dt><dd>{}</dd></dl>"]
)
def test_a_run_of_blank_lines_in_code(tmp_path: Path, wrapper: str) -> None:
    """MarkItDown collapses a run of blank lines in its Markdown to one, a
    top-level code block's included.  In a quotation each blank line of the
    code is a `>` line, which it does not see as blank: the code keeps both,
    as the document has it.  In a definition a blank line is blank, and the
    run is one, as at the top level."""
    code = "one\n\n\ntwo"
    page = _page(tmp_path, wrapper.format(f"<pre>{code}</pre>"))
    assert _top_level(tmp_path, code) == "one\n\ntwo\n"
    assert _code(page) == (
        ["one\n\n\ntwo\n"] if "blockquote" in wrapper else ["one\n\ntwo\n"]
    )


@pytest.mark.parametrize(
    "wrapper",
    [
        "<blockquote>{}</blockquote>",
        "<blockquote><blockquote>{}</blockquote></blockquote>",
        "<ul><li>A<blockquote>{}</blockquote></li></ul>",
        "<dl><dt>t</dt><dd>{}</dd></dl>",
        "<dl><dt>t</dt><dd>d{}</dd></dl>",
        "<blockquote><dl><dt>t</dt><dd>{}</dd></dl></blockquote>",
        "<dl><dt>t</dt><dd><blockquote>{}</blockquote></dd></dl>",
    ],
)
def test_markup_after_a_blank_line_in_code_is_text(
    tmp_path: Path, wrapper: str
) -> None:
    """Measured before, in each of these: a blank line ended the paragraph the
    fence was read in, and what followed was Markdown, raw HTML included.  An
    image typed in a code block became one, and the capture fetched its URL
    (the class of docs/09 P28)."""
    tag = '<img src="https://tracker.invalid/x.png">'
    page = _page(tmp_path, wrapper.format(f"<pre>a\n\n{html.escape(tag)}</pre>"))
    assert BeautifulSoup(page, "html.parser")("img") == [], page
    assert _code(page) == [f"a\n\n{tag}\n"]


@pytest.mark.parametrize(
    ("inline", "container"),
    list(
        itertools.product(
            [
                "<a href='x'>{}</a>",
                "<em>{}</em>",
                "<strong>{}</strong>",
                "<del>{}</del>",
                "<a href='x'><em>{}</em></a>",
            ],
            [
                "<blockquote>{}</blockquote>",
                "<blockquote><blockquote>{}</blockquote></blockquote>",
                "<dl><dt>t</dt><dd>{}</dd></dl>",
                "<dl><dt>t</dt><dd>d{}</dd></dl>",
            ],
        )
    ),
)
def test_markup_in_code_in_a_container_in_a_line_of_text_is_text(
    tmp_path: Path, inline: str, container: str
) -> None:
    """A quotation or a definition in a link or emphasis is written into that
    line of text, which closes after the code's last line.  Measured on
    master: the `<img>` after a blank line in the code was an image on the
    page.  Here it is text in code; the line's closing `](x)` or `*` is in the
    code too (docs/09 P47-4), so only the payload is held."""
    tag = '<img src="https://tracker.invalid/x.png">'
    pre = f"<pre>a\n\n{html.escape(tag)}</pre>"
    soup = BeautifulSoup(
        _page(tmp_path, "<p>" + inline.format(container.format(pre)) + "</p>"),
        "html.parser",
    )
    assert soup("img") == [], soup
    hosts = soup.find_all(string=lambda text: "tracker.invalid" in text)
    assert hosts and all(host.find_parent(["code", "pre"]) for host in hosts), soup


# --------------------------------------------------------------------------- #
# What is written as before
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("body", "markdown_"),
    [
        ("<pre>code\n  x</pre>", "```\ncode\n  x\n```"),
        ("<div><pre>```\nfenced\n```</pre></div>", "````\n```\nfenced\n```\n````"),
        ("<blockquote><p>q</p><ul><li>x</li></ul></blockquote>", "> q\n>\n> * x"),
        ("<blockquote><blockquote>a</blockquote>b</blockquote>", "> > a\n>\n> b"),
        ("<dl><dt>t</dt><dd><p>d</p><p>e</p></dd></dl>", "t\n:   d\n\n    e"),
    ],
)
def test_what_holds_no_code_in_a_quotation_or_a_definition_is_written_as_before(
    tmp_path: Path, body: str, markdown_: str
) -> None:
    """Code outside any container, and quotations and definitions without
    code, are markdownify's Markdown as before, fence lengths included
    (docs/09 P28)."""
    assert _converted(tmp_path, body) == f"Top\n\n{markdown_}\n\nEnd"


@pytest.mark.parametrize(
    ("wrapper", "container", "held"),
    [
        ("<blockquote><a href='x'>{}</a></blockquote>", "blockquote", "a"),
        ("<blockquote><em>{}</em></blockquote>", "blockquote", "em"),
        ("<dl><dt>t</dt><dd><strong>{}</strong></dd></dl>", "dd", "strong"),
    ],
)
def test_code_that_markdownify_writes_into_a_line_is_the_lines_inline_code(
    tmp_path: Path, wrapper: str, container: str, held: str
) -> None:
    """A link or emphasis writes what it holds into a line of text, where no
    code block can begin.  There the code is not a block of the quotation's
    or the definition's: it is the line's inline code, line for line, in a
    container as outside one (docs/09 P59)."""
    body = wrapper.format(f"<pre>{CODE}</pre>")
    converted = _converted(tmp_path, body)
    assert "```" not in converted, converted
    assert f'<code class="{CODE_LINES_CLASS}">' in converted, converted
    soup = BeautifulSoup(_page(tmp_path, body), "html.parser")
    assert soup("pre") == []
    (code,) = soup("code")
    assert code.parent is not None and code.parent.name == held
    assert code.find_parent(container) is not None
    assert code.get("class") == [CODE_LINES_CLASS]
    assert code.get_text() == CODE


def test_code_in_a_table_cell_in_a_quotation_is_the_cells_as_before(
    tmp_path: Path,
) -> None:
    """A Markdown table cell is one line, so markdownify writes its code there
    as inline code; in a quotation, as outside one."""
    table = "<table><tr><th>h</th></tr><tr><td><pre>a\nb</pre></td></tr></table>"
    alone = _converted(tmp_path, table)
    quoted = _converted(tmp_path, f"<blockquote>{table}</blockquote>")
    (row,) = [line for line in alone.splitlines() if "a" in line and "|" in line]
    assert f"> {row.strip()}" in quoted, quoted
    assert not PRIVATE_USE.search(quoted)


def test_code_in_a_definition_in_an_item_is_written_as_before(tmp_path: Path) -> None:
    """Python-Markdown reads a list item's indentation before a definition's,
    so a block indented in a definition in an item is the item's: the code
    reached the page outside its definition, four spaces deeper.  There the
    fence is kept, and the page holds the inline code it did (docs/09
    P47-4)."""
    body = f"<ul><li>A<dl><dt>t</dt><dd><pre>{CODE}</pre></dd></dl></li></ul>"
    assert "```\n" in _converted(tmp_path, body)
    soup = BeautifulSoup(_page(tmp_path, body), "html.parser")
    assert soup("pre") == []
    code = soup.find("code")
    assert code is not None and code.find_parent("dd") is not None


def test_code_of_blank_lines_in_a_quotation_is_nothing(tmp_path: Path) -> None:
    """An indented code block cannot be empty.  Measured before: the fence
    printed as two paragraphs of three backticks."""
    page = _page(tmp_path, "<blockquote><p>q</p><pre>\n  \n</pre></blockquote>")
    assert _code(page) == []
    quotation = BeautifulSoup(page, "html.parser").blockquote
    assert quotation is not None and quotation.get_text().split() == ["q"]


def test_code_in_code_in_a_quotation_leaves_no_mark(tmp_path: Path) -> None:
    for body in (
        "<blockquote><pre>x<pre>y</pre></pre></blockquote>",
        "<dl><dt>t</dt><dd><code><pre>y</pre></code></dd></dl>",
        "<blockquote><h3><pre>y</pre></h3></blockquote>",
    ):
        assert not PRIVATE_USE.search(_converted(tmp_path, body)), body


# --------------------------------------------------------------------------- #
# Any tree
# --------------------------------------------------------------------------- #

#: How many seeded trees `test_code_anywhere_in_any_container_reaches_the_page`
#: draws.
TREES = 40


@pytest.mark.parametrize("seed", range(TREES))
def test_code_anywhere_in_any_container_reaches_the_page(
    tmp_path: Path, seed: int
) -> None:
    """Quotations, definition lists, lists of both kinds and divs three deep,
    each holding up to three pieces in any order: text, a paragraph, a
    container, a block of code of any kind above but a run of blank lines.
    Each `<pre>` must be the top level's, and the page's containers those of
    the same tree with a paragraph for each `<pre>`.  A definition list is
    not drawn inside an item, where Python-Markdown loses a definition's later
    blocks with or without code (docs/09 P47-4)."""
    rng = random.Random(seed)
    codes = list(CODES.values())
    used: list[str] = []

    def build(depth: int, in_item: bool) -> str:
        pieces = []
        for _ in range(rng.randint(1, 3)):
            word = f"w{rng.randint(0, 99)}"
            shapes = ["text", "paragraph", "code"]
            if depth < 3:
                shapes += ["quotation", "div", "list"] + (
                    [] if in_item else ["definitions"]
                )
            shape = rng.choice(shapes)
            if shape == "code":
                code = rng.choice(codes)
                used.append(code)
                pieces.append(f"<pre>{html.escape(code)}</pre>")
            elif shape == "paragraph":
                pieces.append(f"<p>{word}</p>")
            elif shape == "text":
                pieces.append(f" {word} ")
            elif shape == "quotation":
                pieces.append(f"<blockquote>{build(depth + 1, False)}</blockquote>")
            elif shape == "div":
                pieces.append(f"<div>{build(depth + 1, in_item)}</div>")
            elif shape == "definitions":
                pieces.append(
                    "<dl>"
                    + "".join(
                        f"<dt>t{rng.randint(0, 9)}</dt><dd>{build(depth + 1, False)}</dd>"
                        for _ in range(rng.randint(1, 2))
                    )
                    + "</dl>"
                )
            else:
                kind = rng.choice(["ol", "ul"])
                items = "".join(
                    f"<li>{build(depth + 1, True)}</li>"
                    for _ in range(rng.randint(1, 2))
                )
                pieces.append(f"<{kind}>{items}</{kind}>")
        return "".join(pieces)

    body = build(1, False)
    page = _page(tmp_path, body)
    assert _code(page) == [_top_level(tmp_path, code) for code in used], page
    assert BeautifulSoup(page, "html.parser")("img") == [], page
    plain = BeautifulSoup(body, "html.parser")
    for pre in plain("pre"):
        paragraph = plain.new_tag("p")
        paragraph.string = "codeword"
        pre.replace_with(paragraph)
    assert _structure(page) == _structure(_page(tmp_path, str(plain))), body
