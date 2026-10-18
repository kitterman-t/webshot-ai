"""Terms in a row, and a definition after one of more than one block, reach a
converted document's page as its definition list has them (docs/09 P64).

markdownify writes a blank line before every `<dt>`, and Python-Markdown reads
as a definition's terms only the lines straight above its `:` line: of two
terms in a row the first became a paragraph before the list.  It writes each
`<dd>`'s `:` line straight after the one before it, and after a definition of
more than one block Python-Markdown read that line into the definition's last
block: a definition list nested in it, its last paragraph the term.  The
writer now puts terms in a row on adjacent lines, and a blank line between a
definition of more than one block and the next.  Every case below was
measured on the page before and after.
"""

from __future__ import annotations

import itertools
import random
import re

import markdown  # type: ignore[import-untyped]
import pytest
from bs4 import BeautifulSoup

from webshot.acquire.adapters import MARKDOWN_EXTENSIONS, sanitize_markdown_html
from webshot.acquire.markitdown_bridge import _TextHtmlConverter

#: A definition list as the page holds it: each term and definition in order,
#: by tag and text, a nested list's text with the definition it sits in.
Definitions = list[tuple[str, str]]

#: The bridge's marks are drawn from Unicode's private use area.
PRIVATE_USE = re.compile("[-]")


def _converted(body: str) -> str:
    """`body` as the bridge writes an HTML document's Markdown."""
    page = f"<html><body><p>Top</p>{body}<p>End</p></body></html>"
    converted: str = _TextHtmlConverter().convert_string(page).markdown
    return converted


def _render(source: str) -> str:
    """Markdown as the adapter renders a converted document's."""
    rendered: str = sanitize_markdown_html(
        markdown.markdown(
            source, extensions=list(MARKDOWN_EXTENSIONS), output_format="html5"
        )
    )
    return rendered


def _page(body: str) -> str:
    converted = _converted(body)
    assert not PRIVATE_USE.search(converted), converted
    return _render(converted)


def _definition_lists(html: str) -> list[Definitions]:
    """Every definition list in `html`, an outer one before those in it."""
    soup = BeautifulSoup(html, "html.parser")
    return [
        [
            (child.name, " ".join(child.get_text(" ").split()))
            for child in dl.find_all(["dt", "dd"])
            if child.find_parent("dl") is dl
        ]
        for dl in soup.find_all("dl")
    ]


def _dl(*pieces: str) -> str:
    """A definition list of `t…` terms, one-block `d…` definitions, and
    `m…` definitions of two paragraphs, numbered by position."""
    written = []
    for number, piece in enumerate(pieces, 1):
        if piece == "t":
            written.append(f"<dt>t{number}</dt>")
        elif piece == "d":
            written.append(f"<dd>d{number}</dd>")
        else:
            written.append(f"<dd><p>d{number}</p><p>e{number}</p></dd>")
    return "<dl>" + "".join(written) + "</dl>"


# --------------------------------------------------------------------------- #
# The two shapes
# --------------------------------------------------------------------------- #


def test_terms_in_a_row_are_terms_of_one_definition() -> None:
    """Measured before: `t\\n\\nu\\n:   d`, and the page held `<p>t</p>` and
    a list with `u` alone for a term."""
    body = "<dl><dt>t</dt><dt>u</dt><dd><p>d</p><p>e</p></dd></dl>"
    assert _converted(body) == "Top\n\nt\nu\n:   d\n\n    e\n\nEnd"
    assert _definition_lists(_page(body)) == [[("dt", "t"), ("dt", "u"), ("dd", "d e")]]


def test_a_definition_after_one_of_two_blocks_is_a_definition() -> None:
    """Measured before: `e\\n:   f` was one block of the first definition, a
    definition list nested in it with `e` for its term."""
    body = "<dl><dt>t</dt><dd><p>d</p><p>e</p></dd><dd><p>f</p><p>g</p></dd></dl>"
    assert _converted(body) == ("Top\n\nt\n:   d\n\n    e\n\n:   f\n\n    g\n\nEnd")
    page = _page(body)
    assert _definition_lists(page) == [[("dt", "t"), ("dd", "d e"), ("dd", "f g")]]
    assert page.count("<dl>") == 1, page


# --------------------------------------------------------------------------- #
# Every shape, in every container that reads a definition list
# --------------------------------------------------------------------------- #

#: Where a definition list sits: the page itself, a quotation, a definition.
CONTAINERS = {
    "page": lambda dl: dl,
    "quotation": lambda dl: f"<blockquote>{dl}</blockquote>",
    "definition": lambda dl: f"<dl><dt>o</dt><dd><p>o</p>{dl}</dd><dd>z</dd></dl>",
}

#: Every run of terms and definitions of one to four pieces that starts with
#: a term and ends with a definition: what Markdown can hold.
SHAPES = [
    "".join(shape)
    for length in range(2, 5)
    for shape in itertools.product("tdm", repeat=length)
    if shape[0] == "t" and shape[-1] != "t"
]


@pytest.mark.parametrize("container", list(CONTAINERS))
@pytest.mark.parametrize("shape", SHAPES)
def test_every_run_of_terms_and_definitions_reaches_the_page(
    shape: str, container: str
) -> None:
    """Measured before: of these 26 shapes, 10 were right on the page and the
    same 10 in a quotation, and none in a definition, whose own two blocks,
    its text and the list, swallowed the definition after it."""
    body = CONTAINERS[container](_dl(*shape))
    assert _definition_lists(_page(body)) == _definition_lists(body), _converted(body)


@pytest.mark.parametrize("start", [None, 10])
@pytest.mark.parametrize("shape", ["ttd", "tttd", "ttdd", "tdttd"])
def test_terms_in_a_row_in_a_list_item_are_terms(shape: str, start: int | None) -> None:
    """A definition of one block: one of more is read out of the item before
    any of this is reached (docs/09 P62)."""
    opening = f'<ol start="{start}">' if start else "<ul>"
    closing = "</ol>" if start else "</ul>"
    body = f"{opening}<li>x{_dl(*shape)}</li><li>y</li>{closing}"
    assert _definition_lists(_page(body)) == _definition_lists(body)


#: How many seeded lists `test_any_definition_list_reaches_the_page` draws.
LISTS = 60


@pytest.mark.parametrize("seed", range(LISTS))
def test_any_definition_list_reaches_the_page(seed: int) -> None:
    """Groups of one to three terms then one to three definitions, each
    definition text, a line break, paragraphs, a list, a quotation or a
    definition list of its own; on the page, in a quotation or in a
    definition.  Measured before: 57 of the 60 failed."""
    rng = random.Random(seed)
    counter = itertools.count()

    def word() -> str:
        return f"w{next(counter)}"

    def definition(depth: int) -> str:
        shapes = ["text", "break", "two", "three", "ul", "ol", "quote", "text_ul"]
        shape = rng.choice(shapes + (["dl"] if depth < 2 else []))
        return {
            "text": lambda: word(),
            "break": lambda: f"{word()}<br>{word()}",
            "two": lambda: f"<p>{word()}</p><p>{word()}</p>",
            "three": lambda: f"<p>{word()}</p><p>{word()}</p><p>{word()}</p>",
            "ul": lambda: f"<ul><li>{word()}</li><li>{word()}</li></ul>",
            "ol": lambda: f"<ol><li>{word()}</li><li>{word()}</li></ol>",
            "quote": lambda: f"<blockquote><p>{word()}</p></blockquote>",
            "text_ul": lambda: f"<p>{word()}</p><ul><li>{word()}</li></ul>",
            "dl": lambda: f"<p>{word()}</p>{definition_list(depth + 1)}",
        }[shape]()

    def definition_list(depth: int) -> str:
        groups = []
        for _ in range(rng.randint(1, 3)):
            groups += [f"<dt>{word()}</dt>" for _ in range(rng.randint(1, 3))]
            groups += [
                f"<dd>{definition(depth)}</dd>" for _ in range(rng.randint(1, 3))
            ]
        return "<dl>" + "".join(groups) + "</dl>"

    body = rng.choice(list(CONTAINERS.values()))(definition_list(0))
    assert _definition_lists(_page(body)) == _definition_lists(body), _converted(body)


# --------------------------------------------------------------------------- #
# What markdownify already wrote as Python-Markdown reads it, and must still
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("body", "written"),
    [
        pytest.param(
            "<dl><dt>t</dt><dd>d</dd><dt>u</dt><dd>e</dd></dl>",
            "t\n:   d\n\nu\n:   e",
            id="a-term-for-each-definition",
        ),
        pytest.param(
            "<dl><dt>t</dt><dd>d</dd><dd>e</dd></dl>",
            "t\n:   d\n:   e",
            id="definitions-of-one-block",
        ),
        pytest.param(
            "<dl><dt>t</dt><dd><p>d</p><p>e</p></dd><dt>u</dt><dd>f</dd></dl>",
            "t\n:   d\n\n    e\n\nu\n:   f",
            id="a-term-after-a-definition-of-two-blocks",
        ),
        # A line break is no second block, so no blank line goes after it.
        # The `<br>` is the bridge's line break (docs/09 P77), which holds no
        # newline, where markdownify's was `  ` and a newline.
        pytest.param(
            "<dl><dt>t</dt><dd>d<br>e</dd><dd>f</dd></dl>",
            "t\n:   d<br> e\n:   f",
            id="a-definition-after-a-line-break",
        ),
    ],
)
def test_a_list_python_markdown_read_is_written_as_before(
    body: str, written: str
) -> None:
    assert _converted(body) == f"Top\n\n{written}\n\nEnd"
    assert _definition_lists(_page(body)) == _definition_lists(body)


@pytest.mark.parametrize(
    ("body", "written"),
    [
        pytest.param(
            "<dl><dt>t</dt><dt>u</dt></dl>", "t\n\nu", id="no-definition-follows"
        ),
        pytest.param(
            "<dl><dt>t</dt><dt>u</dt><dd> </dd></dl>",
            "t\n\nu",
            id="a-definition-that-writes-nothing",
        ),
        pytest.param(
            "<dl><dt>t</dt>text<dt>u</dt><dd>d</dd></dl>",
            "t\ntext\n\nu\n:   d",
            id="text-between-the-terms",
        ),
        pytest.param(
            "<dl><dt>t</dt><dd></dd><dt>u</dt><dd>d</dd></dl>",
            # markdownify's three newlines, where an element writes nothing.
            "t\n\n\nu\n:   d",
            id="an-empty-definition-between",
        ),
        pytest.param(
            "<dl><dt>t</dt><div><dt>u</dt><dd>d</dd></div></dl>",
            "t\n\nu\n:   d",
            id="a-div-group-after-a-term",
        ),
        pytest.param(
            "<dl><dd><p>d</p><p>e</p></dd><dt> </dt><dt>u</dt><dd>f</dd></dl>",
            ":   d\n\n    e\n\n\nu\n:   f",
            id="a-blank-term-after-a-definition",
        ),
    ],
)
def test_terms_the_document_does_not_put_in_a_row_are_not_joined(
    body: str, written: str
) -> None:
    """A term no definition follows is a paragraph, as before: Markdown has no
    term without one.  Nor is a term joined across text, a definition, or a
    `<div>` group, or onto a definition's line when a blank term stands
    between them."""
    assert _converted(body) == f"Top\n\n{written}\n\nEnd"


def test_a_term_after_a_blank_term_follows_the_term_before_it() -> None:
    """A `<dt>` that writes nothing writes no line, so the terms either side
    of it are in a row."""
    body = "<dl><dt>t</dt><dt> </dt><dt>u</dt><dd>d</dd></dl>"
    assert _converted(body) == "Top\n\nt\nu\n:   d\n\nEnd"


# --------------------------------------------------------------------------- #
# The marks the writer settles a definition list's seams by
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(
            "<table><tr><th>h</th></tr><tr><td>"
            "<dl><dt>t</dt><dt>u</dt><dd><p>d</p><p>e</p></dd><dd>f</dd></dl>"
            "</td></tr></table>",
            id="in-a-table-cell",
        ),
        pytest.param(
            "<pre><dl><dt>t</dt><dt>u</dt><dd>d</dd></dl></pre>", id="in-a-pre"
        ),
        pytest.param(
            "<div><dt>t</dt><dt>u</dt><dd><p>d</p><p>e</p></dd><dd>f</dd></div>",
            id="outside-a-definition-list",
        ),
        pytest.param(
            "<dl><dd><dt>t</dt><dt>u</dt><dd>d</dd></dd></dl>",
            id="in-a-definition-not-a-list",
        ),
    ],
)
def test_no_mark_is_left_where_a_term_is_not_a_lists(body: str) -> None:
    assert not PRIVATE_USE.search(_converted(body)), _converted(body)


def test_a_documents_own_private_use_characters_are_text() -> None:
    """A mark holds a token drawn for each conversion, so text that looks like
    one is left as it is."""
    forged = "" + "0" * 16 + "next"
    body = f"<dl><dt>t</dt><dt>{forged}u</dt><dd>d</dd></dl>"
    page = _render(_converted(body))
    assert _definition_lists(page) == [[("dt", "t"), ("dt", f"{forged}u"), ("dd", "d")]]
