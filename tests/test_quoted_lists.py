"""A list in a quotation in a converted document's item stays the quotation's
(docs/09 P74).

markdownify writes a list as an item's own, on the line after the item's text
and with no blank line after it, whenever an item is anywhere above the list.
In a quotation that holds the list nearer than the item, the quotation's next
line then ran into the list's last item: text after it read `xtail`, and a
list after it was that item's text or more of its items.  The writer now
writes such a list as one in no item is.  Every shape below was measured on
the page before and after; the expected tree is the document's own, never a
recording.
"""

from __future__ import annotations

import itertools
import random
import re
import sys
import zipfile
from pathlib import Path
from typing import Any

import markdown  # type: ignore[import-untyped]
import pytest
from bs4 import BeautifulSoup, NavigableString, Tag

from webshot.acquire.adapters import MARKDOWN_EXTENSIONS, sanitize_markdown_html
from webshot.acquire.markitdown_bridge import _TextHtmlConverter, to_markdown

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:  # the fixture generator lives under tools/
    sys.path.insert(0, str(REPO_ROOT))

FIXTURE_ROOT = REPO_ROOT / "tests" / "fixtures" / "office"

#: The bridge's marks are drawn from Unicode's private use area.
PRIVATE_USE = re.compile("[-]")

#: The elements whose nesting the page must keep; a `<p>` is one text block.
BLOCKS = frozenset({"ul", "ol", "li", "blockquote"})

#: What an element holds: its text blocks, and the blocks above as (tag, held).
Held = list[Any]


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


def _held(element: Tag) -> Held:
    """What `element` holds, each paragraph or run of loose text one block.

    So an item that reads `xtail` holds one block where the document's holds
    `x` in its list and `tail` after it, and a list read as an item's text is
    text, not a list.
    """
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
        elif child.name in BLOCKS:
            end_run()
            held.append((child.name, _held(child)))
        elif child.name == "p":
            end_run()
            run.append(child.get_text())
            end_run()
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


def _in_items(
    inner: str, kinds: tuple[str, ...], lead: str = "A", start: int | None = None
) -> str:
    """`inner` in the first item of a list `len(kinds)` deep, after `lead`."""
    html = f"{lead}{inner}"
    for depth, kind in enumerate(reversed(kinds)):
        opening = f'<{kind} start="{start}">' if kind == "ol" and start else f"<{kind}>"
        html = f"{opening}<li>{html}</li><li>Next{depth}</li></{kind}>"
    return html


def _quoted(inner: str, depth: int = 1) -> str:
    for _ in range(depth):
        inner = f"<blockquote>{inner}</blockquote>"
    return inner


KINDS = ("ol", "ul")
#: Every item depth from one to three, of each kind at each level.
NESTINGS = [
    kinds for depth in (1, 2, 3) for kinds in itertools.product(KINDS, repeat=depth)
]

#: What follows the list in the quotation.  Measured before: only nothing and
#: a paragraph reached the page as the document has them, at every nesting.
AFTER = {
    "nothing": "",
    "text": " and Tail after it ",
    "a paragraph": "<p>Tail</p>",
    "a bulleted list": "<ul><li>y</li></ul>",
    "a numbered list": "<ol><li>y</li></ol>",
    "a quotation": "<blockquote><p>q</p></blockquote>",
}


# --------------------------------------------------------------------------- #
# Every shape, at every depth
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("start", [None, 10], ids=["one-digit", "two-digits"])
@pytest.mark.parametrize("lead", ["A", ""], ids=["after-text", "opening-the-item"])
@pytest.mark.parametrize("depth", [1, 2], ids=["quoted", "quoted-twice"])
@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("after", list(AFTER))
def test_a_list_in_a_quotation_in_an_item_is_the_quotations(
    after: str, kind: str, depth: int, lead: str, start: int | None
) -> None:
    """Measured before: of these 96 shapes, the 64 where text, a list or a
    quotation follows the list were wrong at every one of the 14 nestings;
    after, all 1,344 pages hold the document's tree."""
    inner = _quoted(f"<{kind}><li>x</li><li>z</li></{kind}>{AFTER[after]}", depth)
    for kinds in NESTINGS:
        body = _in_items(inner, kinds, lead, start)
        assert _tree(_page(body)) == _tree(f"<p>Top</p>{body}<p>End</p>"), (
            kinds,
            _converted(body),
        )


def test_the_reported_shapes() -> None:
    """The two shapes the defect was reported with, as the page holds them."""
    page = _page(
        "<ul><li>A<blockquote><ul><li>x</li></ul> tail </blockquote></li></ul>"
    )
    assert re.search(
        r"<blockquote>\s*<ul>\s*<li>x</li>\s*</ul>\s*<p>tail</p>\s*</blockquote>", page
    ), page
    page = _page(
        "<ul><li>A<blockquote><ul><li>x</li></ul><ol><li>y</li></ol></blockquote>"
        "</li></ul>"
    )
    assert re.search(
        r"<blockquote>\s*<ul>\s*<li>x</li>\s*</ul>\s*<ol>\s*<li>y</li>\s*</ol>", page
    ), page


def test_a_list_nested_in_the_quotations_list_is_still_nested() -> None:
    """A list whose nearest holder is an item is the item's, in a quotation
    or not: it is written straight under the item's text, as P40 writes it."""
    body = _in_items(
        _quoted("<ul><li>x<ol><li>y</li></ol></li></ul> tail "
                "<ol><li>z<blockquote><ul><li>w</li></ul> v </blockquote></li></ol>"),
        ("ul",),
    )  # fmt: skip
    assert _tree(_page(body)) == _tree(f"<p>Top</p>{body}<p>End</p>")


# --------------------------------------------------------------------------- #
# The writer
# --------------------------------------------------------------------------- #


def test_a_list_in_a_quotation_in_an_item_is_followed_by_a_blank_line() -> None:
    """markdownify wrote `> + xtail`; the quotation's blank line is `>`."""
    converted = _converted(_in_items(_quoted("<ul><li>x</li></ul> tail "), ("ul",)))
    assert "* A\n  > + x\n  >\n  > tail\n" in converted, converted


def test_two_lists_of_one_kind_in_an_items_first_block_stay_two() -> None:
    """A quotation straight after an item's text is at the marker's width,
    fewer than four spaces in, and the seam between its two lists is found
    there too: the comment that keeps them apart goes on its own line."""
    converted = _converted(
        _in_items(_quoted("<ol><li>x</li></ol><ol><li>y</li></ol>"), ("ol",))
    )
    assert "1. A\n   > 1. x\n   >\n   > <!-- -->\n   >\n   > 1. y\n" in converted, (
        converted
    )


@pytest.mark.parametrize("holder", ["blockquote", "dd"])
def test_a_list_in_a_table_cell_is_written_as_before(holder: str) -> None:
    """In a cell a list is text (docs/09 P40-4), so where it is held is not
    asked.  Asked in `convert_li` and not in `convert_list`, as P62 had it for
    a definition, a list's items went unmarked and the cell ran its last word
    into the word after it: `w2w3` here."""
    inner = "<ol><li>w1</li><li>w2</li></ol> w3 "
    held = (
        f"<blockquote>{inner}</blockquote>"
        if holder == "blockquote"
        else f"<dl><dt>t</dt><dd>{inner}</dd></dl>"
    )
    cell = f"<ol><li><ol><li>A{held}</li></ol></li></ol>"
    body = f"<table><tr><th>h</th></tr><tr><td>{cell}</td></tr></table>"
    converted = _converted(body)
    assert not PRIVATE_USE.search(converted), converted
    row = next(line for line in converted.split("\n") if "w1" in line)
    assert re.search(r"\bw2\s+w3\b", row), row


# --------------------------------------------------------------------------- #
# Any nesting
# --------------------------------------------------------------------------- #

#: How many seeded trees `test_any_nesting_reaches_the_page_as_it_is` draws.
TREES = 80


def _document(rng: random.Random, depth: int = 1) -> str:
    """Lists and quotations up to five deep, each holding text, paragraphs,
    lists and quotations in any order.  Two quotations side by side are left
    out: they merge whether or not an item holds them, which is docs/09 P63's.
    """

    def content(levels: int) -> str:
        pieces: list[str] = []
        last_quoted = False
        for _ in range(rng.randint(1, 3)):
            word = f"w{rng.randint(0, 99)}"
            shape = rng.choice(["text", "paragraph", "list", "quotation"])
            if shape == "quotation" and levels < 5 and not last_quoted:
                pieces.append(f"<blockquote>{content(levels + 1)}</blockquote>")
                last_quoted = True
                continue
            last_quoted = False
            if shape == "list" and levels < 5:
                pieces.append(_document(rng, levels + 1))
            elif shape == "paragraph":
                pieces.append(f"<p>{word}</p>")
            else:
                pieces.append(f" {word} ")
        return "".join(pieces)

    kind = rng.choice(KINDS)
    items = "".join(f"<li>{content(depth)}</li>" for _ in range(rng.randint(1, 3)))
    return f"<{kind}>{items}</{kind}>"


@pytest.mark.parametrize("seed", range(TREES))
def test_any_nesting_reaches_the_page_as_it_is(seed: int) -> None:
    """5,000 such trees passed when this was written; before, 2,517 of them
    reached the page with a different tree."""
    body = _document(random.Random(seed))
    assert _tree(_page(body)) == _tree(f"<p>Top</p>{body}<p>End</p>"), _converted(body)


# --------------------------------------------------------------------------- #
# The fixtures, without the browser
# --------------------------------------------------------------------------- #


def _lists_and_quotations(html: str) -> Held:
    """Each list or quotation that no item or quotation holds, as `_held`."""
    soup = BeautifulSoup(html, "html.parser")
    return [
        (element.name, _held(element))
        for element in soup.find_all(["ul", "ol", "blockquote"])
        if element.find_parent(["li", "blockquote"]) is None
    ]


@pytest.mark.parametrize("name", ["quoted_lists_book.epub", "quoted_lists_archive.zip"])
def test_the_fixtures_hold_their_sections(name: str) -> None:
    """Measured before: only the section with no item around its quotation."""
    from tools.fixtures.office import QUOTED_LISTS_SECTIONS

    converted = to_markdown(FIXTURE_ROOT / name, suffix=Path(name).suffix).markdown
    assert not PRIVATE_USE.search(converted), converted
    page = _render(converted)
    assert "<!--" not in page
    expected = _lists_and_quotations("".join(QUOTED_LISTS_SECTIONS.values()))
    assert _lists_and_quotations(page) == expected


def test_an_html_member_of_a_zip_is_written_the_same_way(tmp_path: Path) -> None:
    archive = tmp_path / "quoted.zip"
    body = _in_items(_quoted("<ul><li>x</li></ul> tail "), ("ul",))
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("page.html", f"<html><body>{body}</body></html>")
    page = _render(to_markdown(archive, suffix=".zip").markdown)
    assert _lists_and_quotations(page) == _lists_and_quotations(body)
