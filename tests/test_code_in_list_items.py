"""A `<pre>` in a converted document's list item keeps its lines (docs/09 P44).

markdownify fences a `<pre>`, the item indents the fence, and Python-Markdown's
`fenced_code` reads a fence only where a line begins.  So the code was read as
inline code in the item's paragraph, and the browser ran its lines and its
indentation together.  The writer now makes it an indented code block in the
item.  Each case compares the item's `<pre>` on the page with the same `<pre>`
written outside a list, whose Markdown is unchanged, and the page's lists with
the HTML's.  Every case below was measured on the page before and after.
"""

from __future__ import annotations

import html
import itertools
import random
import re
from pathlib import Path
from typing import Any

import markdown  # type: ignore[import-untyped]
import pytest
from bs4 import BeautifulSoup, Tag

from webshot.acquire.adapters import MARKDOWN_EXTENSIONS, sanitize_markdown_html
from webshot.acquire.markitdown_bridge import to_markdown

#: A list as the page holds it: its tag, and each item's own text, its code
#: included, with the lists nested in it.
Lists = list[tuple[str, list[tuple[str, list[Any]]]]]

#: The bridge's marks are drawn from Unicode's private use area.
PRIVATE_USE = re.compile("[\ue000-\uf8ff]")

KINDS = ("ol", "ul")


def _converted(tmp_path: Path, body: str) -> str:
    """`body` as the bridge writes an HTML file's Markdown, MarkItDown's own
    clean-up included: it strips each line's end and runs of blank lines,
    which a code block's lines go through too."""
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
    """`code` as the page holds a `<pre>` of it outside any list."""
    (block,) = _code(_page(tmp_path, f"<pre>{html.escape(code)}</pre>"))
    return block


def _own_text(item: Tag) -> str:
    """An item's text, its code's included, but for its nested lists'."""
    words = [
        str(text)
        for text in item.find_all(string=True)
        if not any(
            parent.name in ("ol", "ul") and item in parent.parents
            for parent in text.parents
        )
    ]
    return " ".join(" ".join(words).split())


def _tree(element: Tag) -> tuple[str, list[tuple[str, list[Any]]]]:
    return (
        element.name,
        [
            (
                _own_text(item),
                [
                    _tree(child)
                    for child in item.find_all(["ol", "ul"])
                    if child.find_parent("li") is item
                ],
            )
            for item in element.find_all("li", recursive=False)
        ],
    )


def _lists(page: str) -> Lists:
    """Every list in `page` that is not inside another, outermost first."""
    soup = BeautifulSoup(page, "html.parser")
    return [
        _tree(element)
        for element in soup.find_all(["ol", "ul"])
        if element.find_parent("li") is None
    ]


def _owners(page: str) -> list[str]:
    """For each `<pre>`, the first word of the item that holds it."""
    owners = []
    for pre in BeautifulSoup(page, "html.parser")("pre"):
        item = pre.find_parent("li")
        assert item is not None, pre
        owners.append(_own_text(item).split()[0])
    return owners


# --------------------------------------------------------------------------- #
# Where an item's code sits
# --------------------------------------------------------------------------- #

CODE = "code(1)\n  indented"


def _shapes(tag: str, pre: str) -> dict[str, str]:
    """An item's code beside everything else an item holds, three deep."""
    sub = f"<{tag}><li>Sub</li></{tag}>"
    return {
        "after-text": f"<{tag}><li>A{pre}</li><li>N</li></{tag}>",
        "alone": f"<{tag}><li>{pre}</li><li>N</li></{tag}>",
        "before-text": f"<{tag}><li>{pre}tail</li><li>N</li></{tag}>",
        "between-text": f"<{tag}><li>A{pre}tail</li><li>N</li></{tag}>",
        "after-a-paragraph": f"<{tag}><li><p>A</p>{pre}</li><li>N</li></{tag}>",
        "after-a-nested-list": f"<{tag}><li>A{sub}{pre}</li><li>N</li></{tag}>",
        "before-a-nested-list": f"<{tag}><li>A{pre}{sub}</li><li>N</li></{tag}>",
        "in-a-div": f"<{tag}><li>A<div>{pre}</div></li><li>N</li></{tag}>",
        "in-a-span": f"<{tag}><li>A<span>{pre}</span>tail</li><li>N</li></{tag}>",
        "in-a-figure-caption": (
            f"<{tag}><li>A<figure><figcaption>{pre}</figcaption></figure>tail</li>"
            f"<li>N</li></{tag}>"
        ),
        "depth-2": (
            f"<{tag}><li>A<{tag}><li>B{pre}</li><li>C</li></{tag}></li>"
            f"<li>N</li></{tag}>"
        ),
        "depth-2-alone-then-text": (
            f"<{tag}><li>A<{tag}><li>{pre}</li><li>C</li></{tag}>tail</li>"
            f"<li>N</li></{tag}>"
        ),
        "depth-3": (
            f"<{tag}><li>A<{tag}><li>B<{tag}><li>C{pre}</li><li>D</li></{tag}>"
            f"</li></{tag}></li><li>N</li></{tag}>"
        ),
        "depth-3-then-depth-1-text": (
            f"<{tag}><li>A<{tag}><li>B<{tag}><li>C{pre}</li></{tag}></li>"
            f"</{tag}>tail</li><li>N</li></{tag}>"
        ),
    }


@pytest.mark.parametrize(
    ("tag", "shape"),
    list(itertools.product(KINDS, _shapes("ul", "").keys())),
)
def test_an_items_code_keeps_its_lines(tmp_path: Path, tag: str, shape: str) -> None:
    """Measured before, in every shape: no `<pre>` on the page; the code was
    `<code>code(1) indented</code>` in the item's text."""
    body = _shapes(tag, f"<pre>{CODE}</pre>")[shape]
    page = _page(tmp_path, body)
    assert _code(page) == [_top_level(tmp_path, CODE)], page
    assert _lists(page) == _lists(body), page


@pytest.mark.parametrize("start", [9, 10, 99, 100, 999])
def test_code_under_a_number_of_any_width_keeps_its_lines(
    tmp_path: Path, start: int
) -> None:
    body = f'<ol start="{start}"><li>A<pre>{CODE}</pre></li><li>N</li></ol>'
    page = _page(tmp_path, body)
    assert _code(page) == [_top_level(tmp_path, CODE)]
    assert _lists(page) == _lists(body), page


def test_code_after_a_nested_list_is_the_items_not_the_sub_items(
    tmp_path: Path,
) -> None:
    """Python-Markdown places an indented block by its depth alone, and an
    item's code block is as deep as a nested item's text: without a separator
    it was the sub-item's paragraph, `<li><p>Sub</p><p>code(1)</p></li>`."""
    body = _shapes("ul", f"<pre>{CODE}</pre>")["after-a-nested-list"]
    assert _owners(_page(tmp_path, body)) == ["A"]


def test_two_blocks_of_code_in_an_item_stay_two(tmp_path: Path) -> None:
    """Python-Markdown joins indented code blocks with only blank lines
    between them, as it does an indented block's paragraphs."""
    page = _page(tmp_path, "<ul><li>A<pre>one</pre><pre>two</pre></li></ul>")
    assert _code(page) == ["one\n", "two\n"]


def test_an_item_that_opens_with_code_holds_it_and_nothing_else(
    tmp_path: Path,
) -> None:
    """A code block cannot begin on the marker's line, so the item's text is a
    comment there, which nh3 takes off the page."""
    page = _page(tmp_path, f"<ol><li><pre>{CODE}</pre></li><li>N</li></ol>")
    assert re.search(r"<li>\s*<pre><code>", page), page
    assert _lists(page) == [("ol", [("code(1) indented", []), ("N", [])])]


@pytest.mark.parametrize(
    ("quoted", "codes"),
    [
        (f"<p>q</p><pre>{CODE}</pre>", [CODE]),
        # Written as an item's code, these two were one code block, and the
        # code after the list the sub-item's text: a quotation writes what is
        # beside its code where the item cannot keep them apart.
        ("<pre>one</pre><pre>two</pre>", ["one", "two"]),
        (f"<ul><li>x</li></ul><pre>{CODE}</pre>", [CODE]),
    ],
)
def test_code_in_a_quotation_in_an_item_is_the_quotations(
    tmp_path: Path, quoted: str, codes: list[str]
) -> None:
    """The quotation writes its own code blocks, apart from what is beside
    them (docs/09 P47); before, markdownify's fence after `> ` was inline
    code in the quotation (docs/09 P44-4)."""
    body = f"<ul><li>A<blockquote>{quoted}</blockquote></li><li>N</li></ul>"
    page = _page(tmp_path, body)
    assert _code(page) == [_top_level(tmp_path, code) for code in codes], page
    for pre in BeautifulSoup(page, "html.parser")("pre"):
        assert pre.find_parent("blockquote") is not None, page
        item = pre.find_parent("li")
        assert item is not None and item.find_parent("li") is None, page


# --------------------------------------------------------------------------- #
# What an item's code holds
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
        "<div>\n<!-- open\n<script>x</script>\n</li></ul>\n&amp; &lt;\n"
        "* item\n1. number\n> quoted\n# heading\n[^1]: note\n*[HTML]: abbr\n"
        ": definition\n| a | b |\n|---|---|\n---\n~~~\n{: #anchor}"
    ),
}


@pytest.mark.parametrize(("name", "depth"), list(itertools.product(CODES, (1, 3))))
def test_an_items_code_holds_what_code_outside_a_list_holds(
    tmp_path: Path, name: str, depth: int
) -> None:
    """Each line as the top level keeps it: markdownify trims the blank lines
    at either end, MarkItDown each line's trailing spaces, and Python-Markdown
    expands tabs; an indented block needs no fence, so backticks are text."""
    code = CODES[name]
    body = f"<ul><li>C<pre>{html.escape(code)}</pre></li></ul>"
    for word in "BA"[: depth - 1]:
        body = f"<ul><li>{word}{body}</li></ul>"
    page = _page(tmp_path, body)
    assert _code(page) == [_top_level(tmp_path, code)], page
    assert len(BeautifulSoup(page, "html.parser")("li")) == depth


@pytest.mark.parametrize(
    "holder", ["{}", "<div>{}</div>", "<figure><figcaption>{}</figcaption></figure>"]
)
@pytest.mark.parametrize(
    "opener", ["", "# heading\n", "> quoted\n", "***\n", "<div>\n"]
)
def test_markup_in_an_items_code_is_text(
    tmp_path: Path, holder: str, opener: str
) -> None:
    """Measured before: a blank line, or a line a paragraph gives way to, ended
    the paragraph the fence was read in, and what followed was Markdown, raw
    HTML included.  An image typed in a code block became one, and the
    capture fetched its URL (the class of docs/09 P28)."""
    tag = '<img src="https://tracker.invalid/x.png">'
    code = f"a\n{opener or chr(10)}{tag}"
    pre = holder.format(f"<pre>{html.escape(code)}</pre>")
    page = _page(tmp_path, f"<ul><li>A{pre}</li></ul>")
    assert BeautifulSoup(page, "html.parser")("img") == [], page
    assert _code(page) == [f"{code}\n"]


# --------------------------------------------------------------------------- #
# What already worked, and must still
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("code", "markdown_"),
    [
        ("code\n  x", "```\ncode\n  x\n```"),
        ("```\nfenced\n```", "````\n```\nfenced\n```\n````"),
    ],
)
def test_code_outside_a_list_is_written_as_before(
    tmp_path: Path, code: str, markdown_: str
) -> None:
    """Fenced, longer than any backtick run inside it (docs/09 P28)."""
    converted = _converted(tmp_path, f"<pre>{html.escape(code)}</pre>")
    assert converted == f"Top\n\n{markdown_}\n\nEnd"


def test_code_in_a_table_cell_in_an_item_is_the_cells_as_before(
    tmp_path: Path,
) -> None:
    """A Markdown table cell is one line, so markdownify writes its code there
    as inline code; in an item, as outside one."""
    table = "<table><tr><th>h</th></tr><tr><td><pre>a\nb</pre></td></tr></table>"
    alone = _converted(tmp_path, table)
    in_item = _converted(tmp_path, f"<ul><li>A{table}</li></ul>")
    (row,) = [line for line in alone.splitlines() if "a" in line and "|" in line]
    assert row.strip() in in_item, in_item
    assert not PRIVATE_USE.search(in_item)


@pytest.mark.parametrize(
    ("wrapper", "held"),
    [
        ("<dl><dt>t</dt><dd>{}</dd></dl>", "dd"),
    ],
)
def test_code_that_markdownify_writes_into_a_line_is_written_as_before(
    tmp_path: Path, wrapper: str, held: str
) -> None:
    """A definition writes what it holds into a line of text, where no code
    block can begin; there markdownify's fence is kept, which the page reads
    as inline code, as it did.  An indented block there was plain text.  A
    link's or emphasis's code is inline code of the bridge's own, in an item
    as out of one (docs/09 P59, `tests/test_code_in_inline_text.py`)."""
    body = "<ul><li>A" + wrapper.format(f"<pre>{CODE}</pre>") + "</li></ul>"
    converted = _converted(tmp_path, body)
    assert "```\n" in converted, converted
    page = _page(tmp_path, body)
    code = BeautifulSoup(page, "html.parser").find("code")
    assert code is not None and code.parent is not None, page
    assert code.parent.name == held, page
    assert code.get_text().split() == CODE.split()


def test_code_in_code_in_an_item_leaves_no_mark(tmp_path: Path) -> None:
    for body in (
        "<ul><li>A<pre>x<pre>y</pre></pre></li></ul>",
        "<ul><li>A<code><pre>y</pre></code></li></ul>",
        "<ul><li>A<h3><pre>y</pre></h3></li></ul>",
    ):
        assert not PRIVATE_USE.search(_converted(tmp_path, body)), body


def test_code_of_blank_lines_in_an_item_is_nothing(tmp_path: Path) -> None:
    """An indented code block cannot be empty; the item and the next stay."""
    page = _page(tmp_path, "<ul><li>A<pre>\n  \n</pre></li><li>N</li></ul>")
    assert _lists(page) == [("ul", [("A", []), ("N", [])])]


# --------------------------------------------------------------------------- #
# Any tree
# --------------------------------------------------------------------------- #

#: How many seeded trees `test_code_anywhere_in_any_nesting_reaches_the_page`
#: draws.
TREES = 40


@pytest.mark.parametrize("seed", range(TREES))
def test_code_anywhere_in_any_nesting_reaches_the_page(
    tmp_path: Path, seed: int
) -> None:
    """Lists of both kinds three deep, against the HTML's own tree and the
    top level's code.  An item is up to three pieces in any order: bare text,
    a paragraph, a nested list, a block of code, or a `<div>` of up to three
    more; so code opens an item, ends it, follows a list or other code, inside
    a `<div>` or out of it, and sits at every depth."""
    rng = random.Random(seed)
    codes = list(CODES.values())
    used: list[str] = []

    def pieces(depth: int, wrapped: bool) -> str:
        built = []
        for _ in range(rng.randint(1, 3)):
            word = f"w{rng.randint(0, 99)}"
            shape = rng.choice(["text", "paragraph", "list", "code", "div"])
            if shape == "list" and depth < 3:
                built.append(build(depth + 1))
            elif shape == "code":
                code = rng.choice(codes)
                used.append(code)
                built.append(f"<pre>{html.escape(code)}</pre>")
            elif shape == "div" and not wrapped:
                built.append(f"<div>{pieces(depth, True)}</div>")
            elif shape == "paragraph":
                built.append(f"<p>{word}</p>")
            else:
                built.append(f" {word} ")
        return "".join(built)

    def build(depth: int) -> str:
        items = [pieces(depth, False) for _ in range(rng.randint(1, 3))]
        return "".join(
            [f"<{(kind := rng.choice(KINDS))}>"]
            + [f"<li>{item}</li>" for item in items]
            + [f"</{kind}>"]
        )

    body = build(1)
    page = _page(tmp_path, body)
    assert _code(page) == [_top_level(tmp_path, code) for code in used], page
    assert _lists(page) == _lists(body), _converted(tmp_path, body)
