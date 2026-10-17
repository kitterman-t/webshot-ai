"""A definition list in a converted document's list item stays whole (docs/09 P62).

Python-Markdown hands a block indented under a list to its `indent` processor,
which walks down the list's last item to the block's depth and stops at a
definition list: a definition's second paragraph was read into the item as
code, and its nested list or definition list as a code block's text.  No
indentation and no raw HTML puts a block in such a `<dd>`, so the renderer
gains a processor that reads one there, and the writer gives a list in a
definition the blank line Python-Markdown needs before it.  Every shape below
was measured on the page before and after; the expected tree is the
document's own, never a recording.
"""

from __future__ import annotations

import itertools
import random
import re
from pathlib import Path
from typing import Any

import markdown  # type: ignore[import-untyped]
import pytest
from bs4 import BeautifulSoup, NavigableString, Tag

from webshot.acquire import adapters
from webshot.acquire.adapters import (
    MARKDOWN_EXTENSIONS,
    DefinitionsInItems,
    sanitize_markdown_html,
)
from webshot.acquire.markitdown_bridge import _TextHtmlConverter

#: The bridge's marks are drawn from Unicode's private use area.
PRIVATE_USE = re.compile("[-]")

#: The elements whose nesting the page must keep; a `<p>` is one text block.
BLOCKS = frozenset({"ul", "ol", "li", "dl", "dt", "dd", "blockquote", "pre"})

#: What an element holds: its text blocks, and the blocks above as (tag, held).
Held = list[Any]


def _converted(body: str) -> str:
    """`body` as the bridge writes an HTML document's Markdown."""
    page = f"<html><body><p>Top</p>{body}<p>End</p></body></html>"
    converted: str = _TextHtmlConverter().convert_string(page).markdown
    return converted


def _render(source: str, extensions: list[Any] | None = None) -> str:
    """Markdown as the adapter renders a converted document's."""
    rendered: str = sanitize_markdown_html(
        markdown.markdown(
            source,
            extensions=list(MARKDOWN_EXTENSIONS) if extensions is None else extensions,
            output_format="html5",
        )
    )
    return rendered


def _held(element: Tag) -> Held:
    """What `element` holds, each paragraph or run of loose text one block."""
    held: Held = []
    run: list[str] = []

    def end_run() -> None:
        text = " ".join("".join(run).split())
        if text:
            held.append(text)
        run.clear()

    for child in element.children:
        if isinstance(child, NavigableString):
            if type(child) is NavigableString:  # not a comment
                run.append(str(child))
        elif not isinstance(child, Tag):
            continue
        elif child.name == "pre":
            end_run()
            held.append(("pre", child.get_text().rstrip("\n")))
        elif child.name in BLOCKS:
            end_run()
            held.append((child.name, _held(child)))
        elif child.name == "p":
            end_run()
            run.append(child.get_text())
            end_run()
        elif child.name == "br":
            run.append(" ")
        else:
            run.append(child.get_text())
    end_run()
    return held


def _tree(html: str) -> Held:
    return _held(BeautifulSoup(html, "html.parser"))


def _page(body: str) -> str:
    converted = _converted(body)
    assert not PRIVATE_USE.search(converted), converted
    page = _render(converted)
    assert "<!--" not in page, page
    return page


def _in_items(inner: str, kinds: tuple[str, ...], lead: str = "A") -> str:
    """`inner` in the first item of a list `len(kinds)` deep, after `lead`."""
    html = f"{lead}{inner}"
    for depth, kind in enumerate(reversed(kinds)):
        html = f"<{kind}><li>{html}</li><li>Next{depth}</li></{kind}>"
    return html


KINDS = ("ol", "ul")
#: Every item depth from one to three, of each kind at each level.
NESTINGS = [
    kinds for depth in (1, 2, 3) for kinds in itertools.product(KINDS, repeat=depth)
]

#: What a definition holds, by what each shape shows.
DEFINITIONS = {
    "a second paragraph": "<p>d</p><p>e</p>",
    "three paragraphs": "<p>d</p><p>e</p><p>f</p>",
    "a bulleted list after text": "d<ul><li>x</li><li>y</li></ul>",
    "a numbered list after text": "d<ol><li>x</li><li>y</li></ol>",
    "a paragraph after a list": "<p>d</p><ul><li>x</li></ul><p>e</p>",
    "only a list": "<ul><li>x</li><li>y</li></ul>",
    "a list two deep": "d<ul><li>x<ul><li>z</li></ul></li></ul>",
    "a definition list": "d<dl><dt>t2</dt><dd>d2</dd></dl>",
    "a definition list of two paragraphs": (
        "d<dl><dt>t2</dt><dd><p>d2</p><p>e2</p></dd></dl>"
    ),
    "only a definition list": "<dl><dt>t2</dt><dd>d2</dd></dl>",
    "a quotation": "d<blockquote><p>q</p></blockquote>",
    "a quotation of two paragraphs": "d<blockquote><p>q</p><p>r</p></blockquote>",
    "only a quotation": "<blockquote><p>q</p></blockquote>",
    "one line": "d",
}


# --------------------------------------------------------------------------- #
# Every shape, at every depth
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("lead", ["A", ""], ids=["after-text", "opening-the-item"])
@pytest.mark.parametrize("definition", list(DEFINITIONS))
def test_a_definition_in_an_item_holds_all_it_holds(definition: str, lead: str) -> None:
    """Measured before: of these, only a quotation, a definition that is only
    a list or only a definition list, and one line reached the page whole.
    And where the item opens with the definition list, a definition that is
    only a list kept its first item and gave the rest to the item."""
    dl = f"<dl><dt>t</dt><dd>{DEFINITIONS[definition]}</dd></dl>"
    for kinds in NESTINGS:
        body = _in_items(dl, kinds, lead)
        assert _tree(_page(body)) == _tree(f"<p>Top</p>{body}<p>End</p>"), (
            kinds,
            _converted(body),
        )


@pytest.mark.parametrize(
    "after", ["<p>after</p>", "<ul><li>after</li></ul>", "<ol><li>after</li></ol>"]
)
def test_what_follows_the_definition_list_is_the_items(after: str) -> None:
    """Four spaces in, it is the item's; only eight in is the definition's."""
    dl = "<dl><dt>t</dt><dd><p>d</p><p>e</p></dd><dt>u</dt><dd><p>f</p><p>g</p></dd></dl>"
    for kinds in NESTINGS:
        body = _in_items(dl + after, kinds)
        assert _tree(_page(body)) == _tree(f"<p>Top</p>{body}<p>End</p>"), kinds


def test_the_reported_shapes() -> None:
    """The two shapes the defect was reported with, as the page holds them."""
    page = _page("<ul><li>A<dl><dt>t</dt><dd><p>d</p><p>e</p></dd></dl></li></ul>")
    assert re.search(r"<dd>\s*<p>d</p>\s*<p>e</p>\s*</dd>\s*</dl>\s*</li>", page), page
    assert "<pre>" not in page
    page = _page("<ul><li>A<dl><dt>t</dt><dd>d<ul><li>x</li></ul></dd></dl></li></ul>")
    assert re.search(r"<dd>\s*<p>d</p>\s*<ul>\s*<li>x</li>\s*</ul>\s*</dd>", page), page


# --------------------------------------------------------------------------- #
# The writer
# --------------------------------------------------------------------------- #


def test_a_list_in_a_definition_in_an_item_follows_a_blank_line() -> None:
    """markdownify writes a list in an item straight under the item's text,
    and took a definition's list for one: `d` and `+ x` were one paragraph."""
    converted = _converted(
        _in_items("<dl><dt>t</dt><dd>d<ul><li>x</li></ul></dd></dl>", ("ul",))
    )
    assert "    :   d\n\n        + x\n" in converted, converted


def test_a_definition_list_opening_an_item_moves_to_its_second_block() -> None:
    """On the marker's line, the definition's list's second item is five to
    seven spaces in: Python-Markdown read it as an item of the item's own."""
    body = _in_items(
        "<dl><dt>t</dt><dd><ol><li>x</li><li>y</li></ol></dd></dl>", ("ul",), lead=""
    )
    converted = _converted(body)
    assert "* <!-- -->\n\n    t\n    :   1. x\n        2. y\n" in converted, converted


def test_a_definition_list_opening_an_item_stays_on_the_marker_line() -> None:
    """Where nothing in its first block would be read as an item, it is where
    markdownify put it."""
    converted = _converted(
        _in_items("<dl><dt>t</dt><dd><p>d</p><p>e</p></dd></dl>", ("ol",), lead="")
    )
    assert "1. t\n   :   d\n\n        e\n" in converted, converted


def test_code_in_a_definition_in_an_item_stays_in_the_definition() -> None:
    """Measured before: the code left the definition as an indented block of
    the item, fence lines and all, and took the paragraph after it with it.
    It is still fenced, so Python-Markdown reads it as inline code until the
    writer gives it an indented block there (docs/09 P47)."""
    body = _in_items(
        "<dl><dt>t</dt><dd>d<pre>one\n  two</pre><p>e</p></dd></dl>", ("ul",)
    )
    soup = BeautifulSoup(_page(body), "html.parser")
    definition = soup.find("dd")
    assert isinstance(definition, Tag)
    assert "one" in definition.get_text() and "two" in definition.get_text()
    assert definition.find_all("p")[-1].get_text() == "e"
    assert "```" not in soup.get_text()


# --------------------------------------------------------------------------- #
# The reader
# --------------------------------------------------------------------------- #

#: A block eight spaces in, after a definition list four spaces into an item.
SECOND_PARAGRAPH = "* A\n\n    t\n    :   d\n\n        e\n\n* B\n"


def test_python_markdown_alone_reads_it_into_the_item_as_code() -> None:
    """The upstream fact the processor exists for.  When this fails,
    Python-Markdown reads the block as its own docs say and the processor
    can go."""
    stock = [
        extension
        for extension in MARKDOWN_EXTENSIONS
        if not isinstance(extension, DefinitionsInItems)
    ]
    page = _render(SECOND_PARAGRAPH, stock)
    assert re.search(r"</dl>\s*<pre><code>e\n</code></pre>", page), page


def test_the_processor_reads_it_into_the_definition() -> None:
    page = _render(SECOND_PARAGRAPH)
    assert re.search(r"<dd>\s*<p>d</p>\s*<p>e</p>\s*</dd>", page), page


#: Lines a document's Markdown is made of, for `test_the_processor_claims_only_*`.
LINES = (
    "* a", "1. b", "+ c", "term", ":   def", ": d2", "> q", ">", "para", "```",
    "code", "<!-- -->", "# h", "---", "| x | y |", "|---|---|",
)  # fmt: skip


def _markdown_document(rng: random.Random) -> str:
    lines = []
    for _ in range(rng.randint(2, 14)):
        if rng.random() < 0.25:
            lines.append("")
        else:
            indent = " " * rng.choice([0, 0, 2, 3, 4, 4, 6, 8, 8, 12])
            lines.append(f"{indent}{rng.choice(LINES)}{rng.randint(0, 9)}")
    return "\n".join(lines)


def test_the_processor_claims_only_blocks_past_a_definition_in_an_item() -> None:
    """Over random Markdown, the page differs from Python-Markdown's own only
    where that already has a definition list in an item, and never holds a
    `<dl>` with anything in it but terms and definitions where that did not:
    in a tight item `def_list`'s own walk put a list straight into the
    `<dl>`, which is why the processor leaves a tight item's blocks alone."""
    stock = [
        extension
        for extension in MARKDOWN_EXTENSIONS
        if not isinstance(extension, DefinitionsInItems)
    ]
    differed = 0
    for seed in range(3000):
        source = _markdown_document(random.Random(seed))
        before = markdown.markdown(source, extensions=stock, output_format="html5")
        after = markdown.markdown(
            source, extensions=list(MARKDOWN_EXTENSIONS), output_format="html5"
        )
        if before == after:
            continue
        differed += 1
        soup = BeautifulSoup(before, "html.parser")
        assert any(dl.find_parent("li") for dl in soup.find_all("dl")), source
        assert not [
            child.name
            for dl in BeautifulSoup(after, "html.parser").find_all("dl")
            for child in dl.children
            if isinstance(child, Tag) and child.name not in ("dt", "dd")
        ], source
    # The documents do reach the case: a sweep that never differed proves
    # nothing about it.
    assert differed, "no document had a block past a definition in an item"


def test_an_authors_markdown_reads_the_same_way(tmp_path: Path) -> None:
    """A `.md` file renders through the same configuration, so a definition
    list in an item there reads as one in no item always has."""
    source = tmp_path / "notes.md"
    source.write_text(SECOND_PARAGRAPH, encoding="utf-8")
    page, _, _ = adapters._render_local_file(source)
    assert re.search(r"<dd>\s*<p>d</p>\s*<p>e</p>\s*</dd>", page), page


# --------------------------------------------------------------------------- #
# Any nesting
# --------------------------------------------------------------------------- #

#: How many seeded trees `test_any_nesting_reaches_the_page_as_it_is` draws.
TREES = 80


def _document(rng: random.Random, depth: int = 1, *, quoted: bool = False) -> str:
    """Lists, definition lists and quotations, three deep, in any order.

    Two definition lists or two quotations side by side are left out: they
    merge whether or not an item holds them, which is docs/09 P63's.  So are
    two terms in a row and a definition after one of more than a paragraph,
    which markdownify writes as no definition list Python-Markdown reads,
    anywhere; code, whose place in a definition is docs/09 P47's; and a list
    in a quotation, which markdownify writes as the item's own when an item
    holds the quotation, with no definition list anywhere.
    """

    def word() -> str:
        return f"w{rng.randint(0, 99)}"

    def content(levels: int, *, quoting: bool = quoted) -> str:
        pieces: list[str] = []
        last = ""
        for _ in range(rng.randint(1, 3)):
            shape = rng.choice(["text", "paragraph", "block"])
            if shape == "block" and levels < 3:
                piece = _document(rng, levels + 1, quoted=quoting)
                kind = piece[1:3]
                if kind == last and kind in ("dl", "bl"):
                    continue
                last = kind
            elif shape == "paragraph":
                piece, last = f"<p>{word()} <em>{word()}</em></p>", ""
            else:
                piece, last = f" {word()} ", ""
            pieces.append(piece)
        return "".join(pieces) or f" {word()} "

    shape = rng.choice(["dl"] if quoted else ["ul", "ol", "dl", "blockquote"])
    if shape == "dl":
        # A definition before another is one line, so that markdownify writes
        # the next term after a blank line.
        last = f"<dt>{word()}</dt><dd>{content(depth)}</dd>"
        earlier = f"<dt>{word()}</dt><dd> {word()} </dd>" * rng.randint(0, 1)
        return f"<dl>{earlier}{last}</dl>"
    if shape == "blockquote":
        return f"<blockquote>{content(depth, quoting=True)}</blockquote>"
    items = "".join(f"<li>{content(depth)}</li>" for _ in range(rng.randint(1, 3)))
    return f"<{shape}>{items}</{shape}>"


@pytest.mark.parametrize("seed", range(TREES))
def test_any_nesting_reaches_the_page_as_it_is(seed: int) -> None:
    rng = random.Random(seed)
    body = _in_items(_document(rng), (rng.choice(KINDS),))
    assert _tree(_page(body)) == _tree(f"<p>Top</p>{body}<p>End</p>"), _converted(body)
