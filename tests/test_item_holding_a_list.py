"""A list item that holds only a list keeps its line (docs/09 P45).

docling's HTML backend makes no item of an `<li>` with no text of its own, so
the list inside it hung straight off the outer list. content.md had no line for
the item: after a paragraph, the first lines of its list were a block indented
four spaces, which a CommonMark reader takes for code, and an ordered list
given a start number counted the items after it one short. mammoth writes this
shape for Word bullets at level 1 with none at level 0.

`item_holding_a_list.html` holds each shape. The tests parse content.md with
markdown-it-py (installed with `rich`), a CommonMark reader, and compare its
lists with the fixture's own, item by item.
"""

from __future__ import annotations

import json
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import pytest
from markdown_it import MarkdownIt

from webshot.extract import docling_bridge
from webshot.extract.docling_bridge import (
    EMPTY_ITEM,
    extract,
    mark_empty_items,
    write_empty_items,
)

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "item_holding_a_list.html"

#: A list as (tag, start, items); an item as (its own text, the lists it holds).
List = tuple[str, str, list[Any]]


class _Lists(HTMLParser):
    """Each top-level `<ul>`/`<ol>` between `<h2>` and the next, as a `List`."""

    def __init__(self) -> None:
        super().__init__()
        self.sections: dict[str, list[List]] = {}
        self._section = ""
        self._heading: list[str] | None = None
        self._open: list[Any] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"nav", "table"}:
            self._skip += 1
        elif self._skip:
            return
        elif tag == "h2":
            self._heading = []
        elif tag in {"ul", "ol"}:
            start = dict(attrs).get("start") or "1" if tag == "ol" else ""
            node: List = (tag, start, [])
            if self._open:
                self._open[-1][1].append(node)
            else:
                self.sections.setdefault(self._section, []).append(node)
            self._open.append(node)
        elif tag == "li":
            item: list[Any] = ["", []]
            self._open[-1][2].append(item)
            self._open.append(item)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"nav", "table"}:
            self._skip -= 1
        elif self._skip:
            return
        elif tag == "h2" and self._heading is not None:
            self._section = "".join(self._heading).strip()
            self._heading = None
        elif tag in {"ul", "ol", "li"}:
            self._open.pop()

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        if self._heading is not None:
            self._heading.append(data)
        elif self._open and isinstance(self._open[-1], list) and data.strip():
            self._open[-1][0] = " ".join(f"{self._open[-1][0]} {data}".split())


def _sections(html: str) -> dict[str, list[List]]:
    parser = _Lists()
    parser.feed(html)
    return parser.sections


@pytest.fixture(scope="module")
def result() -> Any:
    return extract(FIXTURE.read_text("utf-8"), [])


def test_content_md_holds_every_list_as_the_page_does(result: Any) -> None:
    """Every list, nested items and numbers included, reads back from content.md
    with a CommonMark reader as the page's own. Before the fix every section but
    the last differed: the items were missing or became code blocks. ("Terms"
    is left out: docling writes a description list as bullets.)"""
    expected = _sections(FIXTURE.read_text("utf-8"))
    rendered = MarkdownIt("commonmark").render(result.markdown)
    got = _sections(rendered)
    assert expected.pop("Terms") and got.pop("Terms")
    assert list(got) == list(expected)
    for section, lists in expected.items():
        assert got[section] == lists, section
    assert "<pre>" not in rendered


@pytest.mark.parametrize(
    "lines",
    [
        pytest.param("-   - Gorvane\n    - Holvish\n- Istrel", id="only-a-list"),
        pytest.param(
            "- Jaskov\n    -   - Kelmar\n        - Lunden\n    - Morvik",
            id="under-text",
        ),
        pytest.param("-   -   - Nalder\n        - Ostrel\n- Pellin", id="chain"),
        pytest.param(
            "1. Quenby\n2.  1. Raskel\n    2. Sorvin\n3. Tallow", id="numbered"
        ),
        pytest.param("5. Umber\n6.  - Varrick\n7. Wendle", id="from-five"),
        pytest.param("-   - Yarrow\n    1. Zennor\n- Abbet", id="two-lists"),
        pytest.param("-   - Brisk\n        - Callow\n- Dunmore", id="terms"),
    ],
)
def test_each_text_surface_writes_the_item_on_its_lists_first_line(
    result: Any, lines: str
) -> None:
    """CommonMark's own spelling of an item holding a list, `- - Gorvane`, with
    the marker padded to docling's four-space indent so the list's next item
    lines up under its first. content.txt and the chunks write list lines as
    content.md does, so all three agree."""
    # A term is bold in content.md and the chunks, which are Markdown too.
    bold = lines.replace("- Brisk", "- **Brisk**")
    chunks = [seed.text for seed in result.chunk_seeds]
    assert f"\n\n{bold}\n\n" in result.markdown
    assert f"\n\n{lines}\n\n" in result.text
    assert bold in chunks


def test_a_marker_line_of_its_own_would_not_do() -> None:
    """Why the item shares its list's first line: an empty marker line cannot
    interrupt a paragraph, so under an item with text CommonMark read it as a
    setext underline, the text above as a heading, and the list as code."""
    reader = MarkdownIt("commonmark")
    bare = reader.render("- Jaskov\n    -\n        - Kelmar\n    - Morvik\n")
    assert "<h2>Jaskov</h2>" in bare and "<pre>" in bare
    shared = reader.render("- Jaskov\n    -   - Kelmar\n    - Morvik\n")
    assert shared == (
        "<ul>\n<li>Jaskov\n<ul>\n<li>\n<ul>\n<li>Kelmar</li>\n</ul>\n</li>\n"
        "<li>Morvik</li>\n</ul>\n</li>\n</ul>\n"
    )


def test_content_json_holds_the_item_empty_and_numbered(result: Any) -> None:
    """The record holds the `<li>` the page has: an item with no text whose child
    is its list, numbered among its siblings. Before, the list was the outer
    list's child, the shape an invalid `<ul><ul>` gives, and "Wendle" was 6."""
    document = json.loads(result.document_json)
    by_ref = {item["self_ref"]: item for item in document["texts"]}
    by_ref.update({group["self_ref"]: group for group in document["groups"]})
    empty = [
        item
        for item in document["texts"]
        if item["label"] == "list_item" and not item["text"]
    ]
    # Harbour/Ledger's in the nav, 1+1+2 in the chain, and one each elsewhere.
    assert len(empty) == 10
    for item in empty:
        assert item["orig"] == ""
        children = [by_ref[child["$ref"]] for child in item["children"]]
        assert children and all(child["label"] == "list" for child in children)
    markers = {
        item["text"]: item["marker"]
        for item in document["texts"]
        if item["label"] == "list_item" and item["text"] in {"Umber", "Wendle"}
    }
    assert markers == {"Umber": "5.", "Wendle": "7."}
    assert [item["marker"] for item in empty if item["marker"]] == ["6."]
    docling_bridge.validate_document_json(result.document_json)


def test_a_nav_menu_item_stays_out_of_the_body(result: Any) -> None:
    """An empty item has no text to tell chrome by. Moved to the body on its own,
    a nav menu's item would be a bare marker in content.md; it goes where its
    items go, and they stay chrome."""
    document = json.loads(result.document_json)
    nav = [
        item
        for item in document["texts"]
        if item["label"] == "list_item" and item["text"] in {"Harbour", "Ledger", ""}
    ]
    assert {item["content_layer"] for item in nav[:3]} == {"furniture"}
    assert "Harbour" not in result.markdown
    assert not result.markdown.startswith("-")


def test_a_table_cell_writes_the_item_before_its_list(result: Any) -> None:
    """docling writes a cell's list on one line; the item is its marker there."""
    assert "| - - Elvar     - Fennick - Galt |" in result.markdown
    assert result.tables[0] == [["Steps"], ["Elvar Fennick  Galt"]]


def test_no_stand_in_reaches_the_record(result: Any) -> None:
    surfaces = [result.document_json, result.markdown, result.text, result.doctags]
    surfaces += [seed.text for seed in result.chunk_seeds]
    surfaces += [cell for grid in result.tables for row in grid for cell in row]
    for surface in surfaces:
        assert EMPTY_ITEM not in surface
        assert "\ufeff" not in surface
    assert result.warnings == []


def test_only_the_items_lines_change(result: Any, monkeypatch: Any) -> None:
    """Against docling's own reading (the page unmarked), content.md differs on
    the lines of these items and on nothing else: each line docling wrote is
    now the item's, and "Wendle" is numbered 7 as on the page, not 6."""
    monkeypatch.setattr(docling_bridge, "mark_empty_items", lambda html: html)
    unmarked = extract(FIXTURE.read_text("utf-8"), []).markdown.split("\n")
    marked = result.markdown.split("\n")
    assert len(marked) == len(unmarked)
    changed = [
        (old, new) for old, new in zip(unmarked, marked, strict=True) if old != new
    ]
    assert changed == [
        ("    - Gorvane", "-   - Gorvane"),
        ("        - Kelmar", "    -   - Kelmar"),
        ("        - Nalder", "-   -   - Nalder"),
        ("    1. Raskel", "2.  1. Raskel"),
        ("    - Varrick", "6.  - Varrick"),
        ("6. Wendle", "7. Wendle"),
        ("    - Yarrow", "-   - Yarrow"),
        ("    - **Brisk**", "-   - **Brisk**"),
        ("| - Elvar     - Fennick - Galt |", "| - - Elvar     - Fennick - Galt |"),
    ]


def test_a_page_with_no_such_item_is_parsed_as_given() -> None:
    plain = (
        '<html><head><meta charset="utf-8"></head><body><h1>T</h1>'
        "<ul><li>Hollin<ul><li>Ibbet</li></ul></li><li></li><li>Jorran</li></ul>"
        "<ol start='3'><li>Kelby</li><li><img src='a.png' alt='x'><ol><li>Lorn</li>"
        "</ol></li></ol><ul><li>Moss</li><ul><li>Nell</li></ul></ul></body></html>"
    )
    assert mark_empty_items(plain) is plain


@pytest.mark.parametrize(
    "item",
    [
        pytest.param("<li><ul><li>a</li></ul></li>", id="list"),
        pytest.param("<li> &nbsp;\n <div><ol><li>a</li></ol></div></li>", id="space"),
        pytest.param(
            '<li><a id="b"></a><b> </b><ul><li>a</li></ul></li>', id="empty-run"
        ),
        pytest.param("<li><dl><dt>a</dt></dl></li>", id="terms"),
    ],
)
def test_an_item_with_no_text_of_its_own_is_marked(item: str) -> None:
    marked = mark_empty_items(f"<ul>{item}</ul>")
    assert marked.startswith("\ufeff")
    assert marked.count(EMPTY_ITEM) == 1
    assert marked.index(EMPTY_ITEM) == len("\ufeff<ul><li>")


@pytest.mark.parametrize(
    "item",
    [
        pytest.param("<li>a<ul><li>b</li></ul></li>", id="text-before"),
        pytest.param("<li><ul><li>b</li></ul>a</li>", id="text-after"),
        pytest.param("<li><img src='a.png'><ul><li>b</li></ul></li>", id="image"),
        pytest.param("<li><input type='checkbox'><ul><li>b</li></ul></li>", id="box"),
        pytest.param("<li><pre>a</pre><ul><li>b</li></ul></li>", id="code"),
        pytest.param("<li><br><ul><li>b</li></ul></li>", id="line-break"),
        pytest.param("<li></li>", id="no-list"),
    ],
)
def test_an_item_with_anything_else_is_left_to_docling(item: str) -> None:
    """docling's own reading of these is kept: an item with text, an image, a
    checkbox, a code block, and an item with nothing in it at all. A `<br>` is
    text to docling, which makes an item of it (its U+E000 line-break character
    as the text), so marking it left the stand-in beside that character.
    `extract` takes out the `<br>`s of an item with no text first (P56), so
    there `<li><br><ul>` reads as `<li><ul>`, which is marked."""
    page = f"<ul>{item}</ul>"
    assert mark_empty_items(page) is page


def test_only_an_item_of_a_list_is_marked() -> None:
    page = (
        "<dl><li><ul><li>a</li></ul></li></dl><menu><li><ul><li>b</li></ul></li></menu>"
    )
    assert mark_empty_items(page) is page


def test_a_stand_in_with_no_line_to_share_keeps_its_marker_alone() -> None:
    """A marker too wide for docling's four-space indent, or nothing deeper on
    the next line: the item keeps a line of its own with its marker."""
    stand_in = f" {EMPTY_ITEM}"
    assert write_empty_items(f"1000.{stand_in}\n    1. a") == "1000.\n    1. a"
    assert write_empty_items(f"-{stand_in}\n\n- a") == "-\n\n- a"
    assert write_empty_items(f"-{stand_in}") == "-"
    assert write_empty_items("- a\n    - b") == "- a\n    - b"


def test_a_stray_stand_in_is_removed_and_reported(monkeypatch: Any) -> None:
    """A stand-in that reached a surface outside its item's line stood in for no
    item there. It is removed, and the record says where, rather than deliver a
    character the page never had."""
    monkeypatch.setattr(docling_bridge, "write_empty_items", lambda text: text)
    stray = extract(FIXTURE.read_text("utf-8"), [])
    assert EMPTY_ITEM not in stray.markdown + stray.text
    assert all(EMPTY_ITEM not in seed.text for seed in stray.chunk_seeds)
    assert stray.warnings == [
        "the stand-in for the text of a list item that holds only a list reached "
        "a field it does not belong in (content.md, content.txt, chunks.jsonl); it "
        "was removed there, and the item may have no line of its own there "
        "(docs/09 P45)."
    ]


@pytest.mark.parametrize("character", ["\ufdd2", "&#xFDD2;", "&#xfdd2", "&#64978;"])
def test_a_page_carrying_the_stand_in_keeps_it_and_says_so(character: str) -> None:
    """A page with U+FDD2 of its own, as the character or a reference to it,
    cannot be marked without losing it. html.unescape drops a noncharacter's
    reference, so the check looks for the reference itself."""
    page = (
        '<html><head><meta charset="utf-8"></head><body><h1>T</h1>'
        f"<p>odd {character} kept</p><ul><li><ul><li>a</li></ul></li></ul></body></html>"
    )
    result = extract(page, [])
    assert "odd \ufdd2 kept" in result.text
    assert "\n    - a" in result.markdown
    assert any("U+FDD2" in warning for warning in result.warnings)
