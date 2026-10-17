"""A list nested in a converted document's item stays nested (docs/09 P40).

markdownify indents what belongs to a list item by the width of the item's
marker: two spaces under `* `, three under `1. `.  Python-Markdown nests only
at four (its `tab_length`), so a sub-list under a bullet became items of the
bullet's own list, one under a number was printed into the item as `* sub`,
and an item's second paragraph left the list with the next item inside it.
The bridge's writer now indents by four, leaves a blank line after a nested
list that more of its item follows, and keeps two lists in one item apart.
Every case below was measured on the page before and after.
"""

from __future__ import annotations

import itertools
import random
import re
import zipfile
from pathlib import Path
from typing import Any

import markdown  # type: ignore[import-untyped]
import pytest
from bs4 import BeautifulSoup, Tag

from webshot.acquire import adapters
from webshot.acquire.adapters import MARKDOWN_EXTENSIONS, sanitize_markdown_html
from webshot.acquire.markitdown_bridge import (
    ITEM_INDENT,
    _TextHtmlConverter,
    to_markdown,
)

#: A list as the page holds it: its tag, and each item's own text with the
#: lists nested in it.
Lists = list[tuple[str, list[tuple[str, list[Any]]]]]

#: The bridge's marks are drawn from Unicode's private use area.
PRIVATE_USE = re.compile("[\ue000-\uf8ff]")


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


def _own_text(item: Tag) -> str:
    """An item's text, but for the text of the lists nested in it."""
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


def _lists(html: str) -> Lists:
    """Every list in `html` that is not inside another, outermost first."""
    soup = BeautifulSoup(html, "html.parser")
    return [
        _tree(element)
        for element in soup.find_all(["ol", "ul"])
        if element.find_parent("li") is None
    ]


def _page(body: str) -> str:
    converted = _converted(body)
    assert not PRIVATE_USE.search(converted), converted
    page = _render(converted)
    assert "<!--" not in page, page
    return page


def _list(tag: str, *items: str, start: int | None = None) -> str:
    opening = f'<{tag} start="{start}">' if start is not None else f"<{tag}>"
    return opening + "".join(f"<li>{item}</li>" for item in items) + f"</{tag}>"


def _item(text: str, *lists: tuple[str, list[Any]]) -> tuple[str, list[Any]]:
    return (text, list(lists))


# --------------------------------------------------------------------------- #
# Depth and kind
# --------------------------------------------------------------------------- #

KINDS = ("ol", "ul")


@pytest.mark.parametrize(("outer", "inner"), list(itertools.product(KINDS, KINDS)))
def test_a_list_in_an_item_is_nested_in_it(outer: str, inner: str) -> None:
    """Measured before: `ul>ul` and `ol>ol` became one list of four items;
    `ul>ol` and `ol>ul` printed the sub-items into the parent's text."""
    body = _list(outer, "Parent" + _list(inner, "Child one", "Child two"), "Next")
    page = _page(body)
    assert _lists(page) == [
        (
            outer,
            [
                _item("Parent", (inner, [_item("Child one"), _item("Child two")])),
                _item("Next"),
            ],
        )
    ]
    assert "<p>" not in page.split("<p>Top</p>")[1].split("<p>End</p>")[0], (
        "a nested list that nothing follows leaves its items tight"
    )


@pytest.mark.parametrize(
    "kinds", list(itertools.product(KINDS, repeat=3)), ids="-".join
)
def test_three_levels_of_any_kinds_are_nested(kinds: tuple[str, str, str]) -> None:
    first, second, third = kinds
    body = _list(first, "A" + _list(second, "B" + _list(third, "C"), "B2"), "A2")
    assert _lists(_page(body)) == [
        (
            first,
            [
                _item("A", (second, [_item("B", (third, [_item("C")])), _item("B2")])),
                _item("A2"),
            ],
        )
    ]


def test_each_level_is_indented_by_python_markdowns_tab_length() -> None:
    markdown_text = _converted(_list("ul", "A" + _list("ol", "B" + _list("ul", "C"))))
    assert f"\n{ITEM_INDENT}1. B\n{ITEM_INDENT * 2}+ C" in markdown_text


@pytest.mark.parametrize("start", [1, 9, 10, 99, 100, 999, 1000])
def test_a_number_of_any_width_nests_what_is_under_it(start: int) -> None:
    """Under `10.` a nested list was four spaces in, so it nested by chance:
    why docs/09 P30-1's `<ol start="12">` fixture did and `1.` did not."""
    body = _list(
        "ol",
        "First" + _list("ul", "sub" + _list("ol", "deep", start=start)),
        "Second" + _list("ul", "sub two"),
        start=start,
    )
    assert _lists(_page(body)) == [
        (
            "ol",
            [
                _item("First", ("ul", [_item("sub", ("ol", [_item("deep")]))])),
                _item("Second", ("ul", [_item("sub two")])),
            ],
        )
    ]


# --------------------------------------------------------------------------- #
# Items of more than one block
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("start", [None, 10])
def test_an_items_second_paragraph_stays_in_the_item(start: int | None) -> None:
    """Measured before: `Two` left the list as a paragraph, and the next
    item went with it, its number printed: `Two 2. Three`."""
    body = _list("ol", "<p>One</p><p>Two</p>", "<p>Three</p>", start=start)
    page = _page(body)
    assert _lists(page) == [("ol", [_item("One Two"), _item("Three")])]
    assert re.search(r"<li>\s*<p>One</p>\s*<p>Two</p>\s*</li>", page), page


def test_a_paragraph_after_a_nested_list_stays_in_the_item() -> None:
    body = _list("ul", "<p>Intro</p>" + _list("ul", "sub") + "<p>After</p>", "Next")
    assert _lists(_page(body)) == [
        ("ul", [_item("Intro After", ("ul", [_item("sub")])), _item("Next")])
    ]


def test_text_after_a_nested_list_is_the_items_not_the_sub_items() -> None:
    """markdownify writes it straight after the sub-list's last item, with no
    line break: `+ subtail text`."""
    body = _list("ul", "A" + _list("ul", "sub") + "tail text", "B")
    assert _lists(_page(body)) == [
        ("ul", [_item("A tail text", ("ul", [_item("sub")])), _item("B")])
    ]


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        pytest.param(
            _list("ul", "<p>One</p>Two" + _list("ul", "sub"), "Next"),
            [("ul", [_item("One Two", ("ul", [_item("sub")])), _item("Next")])],
            id="after-a-second-paragraph",
        ),
        pytest.param(
            _list("ul", "A" + _list("ul", "s") + "tail" + _list("ul", "t")),
            [("ul", [_item("A tail", ("ul", [_item("s")]), ("ul", [_item("t")]))])],
            id="after-text-after-a-list",
        ),
    ],
)
def test_a_list_after_an_items_later_block_is_nested(
    body: str, expected: Lists
) -> None:
    """Python-Markdown nests a list with no blank line before it only straight
    after the item's first block; after a later one it read `Two + sub`."""
    assert _lists(_page(body)) == expected


# --------------------------------------------------------------------------- #
# Two lists in one item
# --------------------------------------------------------------------------- #


def test_a_list_ending_in_an_item_of_two_blocks_still_meets_the_next_apart() -> None:
    """Such an item ends its list with a blank line, and P32 keeps two lists
    of one kind apart by what stands between their marks: still one comment."""
    body = (
        _list("ol", "A", "<p>B one</p><p>B two</p>")
        + _list("ol", "C")
        + _list("ul", "<p>D</p><p>D2</p>")
        + _list("ul", "E" + _list("ul", "e"))
    )
    assert _lists(_page(body)) == [
        ("ol", [_item("A"), _item("B one B two")]),
        ("ol", [_item("C")]),
        ("ul", [_item("D D2")]),
        ("ul", [_item("E", ("ul", [_item("e")]))]),
    ]


@pytest.mark.parametrize(("first", "second"), list(itertools.product(KINDS, KINDS)))
def test_two_lists_in_one_item_stay_two(first: str, second: str) -> None:
    """mammoth writes a Word item with a bulleted sub-item and a numbered one
    as two lists in the item.  markdownify joins them with no line between,
    so even nested they were one list, or the second its text (P32-5)."""
    body = _list(
        "ol", "Alpha" + _list(first, "sub a") + _list(second, "sub b"), "Bravo"
    )
    assert _lists(_page(body)) == [
        (
            "ol",
            [
                _item("Alpha", (first, [_item("sub a")]), (second, [_item("sub b")])),
                _item("Bravo"),
            ],
        )
    ]


def test_three_lists_of_one_kind_in_one_item_stay_three() -> None:
    body = _list("ul", "A" + _list("ul", "a") + _list("ul", "b") + _list("ul", "c"))
    assert _lists(_page(body)) == [
        (
            "ul",
            [
                _item(
                    "A",
                    ("ul", [_item("a")]),
                    ("ul", [_item("b")]),
                    ("ul", [_item("c")]),
                )
            ],
        )
    ]


# --------------------------------------------------------------------------- #
# Items with no text of their own
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        pytest.param(
            _list("ul", _list("ul", "x"), "B"),
            [("ul", [_item("", ("ul", [_item("x")])), _item("B")])],
            id="one-sub-item",
        ),
        pytest.param(
            # mammoth's shape for Word bullets at level 1 with none at level 0:
            # the sub-items after the first became the parent list's.
            _list("ul", _list("ul", "x", "y", "z"), "B"),
            [
                (
                    "ul",
                    [
                        _item("", ("ul", [_item("x"), _item("y"), _item("z")])),
                        _item("B"),
                    ],
                )
            ],
            id="three-sub-items",
        ),
        pytest.param(
            _list("ol", _list("ol", "x" + _list("ul", "deep"), "y"), "B"),
            [
                (
                    "ol",
                    [
                        _item(
                            "",
                            ("ol", [_item("x", ("ul", [_item("deep")])), _item("y")]),
                        ),
                        _item("B"),
                    ],
                )
            ],
            id="its-first-sub-item-nests-too",
        ),
        pytest.param(
            _list("ul", "A" + _list("ul", _list("ul", "x", "y"), "C")),
            [
                (
                    "ul",
                    [
                        _item(
                            "A",
                            (
                                "ul",
                                [
                                    _item("", ("ul", [_item("x"), _item("y")])),
                                    _item("C"),
                                ],
                            ),
                        )
                    ],
                )
            ],
            id="nested-itself",
        ),
    ],
)
def test_an_item_that_is_only_a_list_holds_all_of_it(
    body: str, expected: Lists
) -> None:
    assert _lists(_page(body)) == expected


# --------------------------------------------------------------------------- #
# What already worked, and must still
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("start", [None, 10])
def test_a_quotation_in_an_item_is_still_a_quotation_in_it(start: int | None) -> None:
    """It nested at markdownify's two spaces and not at four, so the item's
    first block keeps a shallower indentation; under `10.` it now nests too."""
    page = _page(
        _list("ol", "A<blockquote><p>quoted</p></blockquote>", "B", start=start)
    )
    assert re.search(r"<li>A\s*<blockquote>\s*<p>quoted</p>", page), page


def test_a_list_in_a_quotation_is_nested_in_it() -> None:
    page = _page(
        "<blockquote>" + _list("ul", "A" + _list("ul", "sub")) + "</blockquote>"
    )
    assert _lists(page) == [("ul", [_item("A", ("ul", [_item("sub")]))])]
    assert "<blockquote>" in page


def test_a_flat_list_is_written_as_markdownify_writes_it() -> None:
    """One line an item: nothing to indent, so nothing changes."""
    assert _converted(_list("ol", "One", "Two")) == "Top\n\n1. One\n2. Two\n\nEnd"


def test_a_line_break_in_a_nested_item_stays_in_it() -> None:
    page = _page(_list("ul", "A" + _list("ul", "line one<br/>line two")))
    assert _lists(page) == [("ul", [_item("A", ("ul", [_item("line one line two")]))])]


# --------------------------------------------------------------------------- #
# The marks the writer finds nested items by
# --------------------------------------------------------------------------- #


def test_no_mark_is_left_where_a_list_cannot_be_one() -> None:
    """A nested list in a table cell or a `<pre>` is written on the cell's line
    or as code; its items are still marked, and still unmarked."""
    cell = (
        "<table><tr><th>h</th></tr><tr><td>"
        + _list("ul", "x" + _list("ol", "y"))
        + "</td></tr></table>"
    )
    code = "<pre>" + _list("ul", "x" + _list("ul", "y")) + "</pre>"
    for body in (_list("ul", "A" + cell), _list("ul", "A" + code), cell, code):
        assert not PRIVATE_USE.search(_converted(body)), body


def test_a_documents_own_private_use_characters_are_text() -> None:
    """A mark holds a token drawn for each conversion, so text that looks like
    one is left as it is."""
    forged = "\ue002" + "0" * 16 + "ul1\ue003"
    page = _render(_converted(_list("ul", f"A {forged} B" + _list("ul", "sub"))))
    assert _lists(page) == [("ul", [_item(f"A {forged} B", ("ul", [_item("sub")]))])]


#: How many seeded trees `test_any_nesting_reaches_the_page_as_it_is` draws.
TREES = 60


@pytest.mark.parametrize("seed", range(TREES))
def test_any_nesting_reaches_the_page_as_it_is(seed: int) -> None:
    """Lists of both kinds three deep, against the HTML's own tree.  An item
    is up to three pieces in any order: bare text, a paragraph, a nested list;
    so it has text of its own or none, lists side by side, text after a list,
    and a list after a later block."""
    rng = random.Random(seed)

    def build(depth: int) -> str:
        items = []
        for _ in range(rng.randint(1, 3)):
            pieces = []
            for _ in range(rng.randint(1, 3)):
                word = f"w{rng.randint(0, 99)}"
                shape = rng.choice(["text", "paragraph", "list"])
                if shape == "list" and depth < 3:
                    pieces.append(build(depth + 1))
                elif shape == "paragraph":
                    pieces.append(f"<p>{word}</p>")
                else:
                    pieces.append(f" {word} ")
            items.append("".join(pieces))
        return _list(rng.choice(KINDS), *items)

    body = build(1)
    assert _lists(_page(body)) == _lists(body), _converted(body)


# --------------------------------------------------------------------------- #
# Markdown that is the author's, not a converter's
# --------------------------------------------------------------------------- #

AUTHORS_MARKDOWN = "* a\n  * b\n\n1. one\n   * two\n"


def test_a_markdown_file_is_rendered_as_its_author_wrote_it(tmp_path: Path) -> None:
    """A `.md` file never reaches the bridge: its two-space nesting is the
    author's, read as Python-Markdown reads it, flat."""
    source = tmp_path / "notes.md"
    source.write_text(AUTHORS_MARKDOWN, encoding="utf-8")
    page, _, _ = adapters._render_local_file(source)
    assert _lists(page) == [
        ("ul", [_item("a"), _item("b")]),
        ("ol", [_item("one * two")]),
    ]


def test_a_markdown_member_of_a_zip_is_its_text(tmp_path: Path) -> None:
    archive = tmp_path / "notes.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("notes.md", AUTHORS_MARKDOWN)
    assert AUTHORS_MARKDOWN.rstrip("\n") in to_markdown(archive, suffix=".zip").markdown
