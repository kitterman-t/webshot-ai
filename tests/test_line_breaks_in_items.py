"""A `<br>` in a list item, heading, caption or `<pre>` (docs/09 P56).

docling's HTML backend writes each `<br>` as U+E000 while it parses and turns
it back into a line break only in a paragraph's text. In a list item, a term
or description, a task, a heading, a caption and a `<pre>` it stayed, and
reached content.json, content.md, content.txt, the doctags and every chunk.

The backend reads each of those but a `<pre>` onto one line, joining every
run of whitespace in it into a space, so the bridge writes a `<br>` there as
whitespace and one in a `<pre>` as a line ending. Each shape is checked
against the same page with the `<br>` written as that whitespace: the two must
be one record.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from webshot.extract.docling_bridge import (
    LINE_BREAK,
    drop_lone_line_breaks,
    extract,
    write_line_breaks,
)

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "line_breaks_in_items.html"


def _page(body: str) -> str:
    return f"<html><body><h1>T</h1>{body}</body></html>"


def _record(html: str) -> dict[str, Any]:
    """Every surface `extract` returns, but the origin hash, which is the page's."""
    result = extract(html, [])
    document = json.loads(result.document_json)
    del document["origin"]
    return {
        "content.json": document,
        "content.md": result.markdown,
        "content.txt": result.text,
        "doctags": result.doctags,
        "tables": result.tables,
        "chunks": [
            (seed.text, seed.headings, seed.doc_items, seed.kind)
            for seed in result.chunk_seeds
        ],
        "warnings": result.warnings,
    }


def _carrying(record: dict[str, Any]) -> list[str]:
    return [
        name
        for name, value in record.items()
        if LINE_BREAK in json.dumps(value, ensure_ascii=False)
    ]


#: Where the backend reads a `<br>` onto one line, each against the same
#: element holding a space.
ONE_LINE = {
    "item-end": "<ul><li>Halder<br></li></ul>",
    "item-middle": "<ul><li>Jask<br>ov</li></ul>",
    "item-start": "<ul><li><br>Lunden</li></ul>",
    "item-run": "<ul><li>Kel<br><br>mar</li></ul>",
    "item-spaced": "<ul><li>Kel <br> mar</li></ul>",
    "item-parts": "<ul><li><b>Morvik</b><br>Nalder</li></ul>",
    "item-in-a-part": "<ul><li><b>Mor<br>vik</b> Nalder</li></ul>",
    "item-link": '<ul><li><a href="https://example.test/">Ostrel<br>Pellin</a></li></ul>',
    "item-code": "<ul><li><code>quen<br>by</code></li></ul>",
    "item-code-part": "<ul><li>see <code>quen<br>by</code> now</li></ul>",
    "item-paragraphs": "<ul><li><p>Raskel</p><p>Sorvin<br>Tallow</p></li></ul>",
    "item-subscript": "<ul><li>H<sub>2</sub><br>O</li></ul>",
    "item-before-a-subscript": "<ul><li><br><sub>2</sub>x</li></ul>",
    "item-nested": "<ul><li>Umber<br><ul><li>Varrick<br>Wendle</li></ul></li></ul>",
    "numbered": "<ol><li>Sorvin<br>Tallow</li></ol>",
    "task": '<ul><li><input type="checkbox" checked>Yarrow<br>Zennor</li></ul>',
    "terms": "<dl><dt>Abbet<br>Brisk</dt><dd>Callow<br>Dunmore</dd></dl>",
    "title": "<h1>Ti<br>tle</h1><p>body</p>",
    "heading": "<h2>Head<br>line</h2><p>body</p>",
    "heading-link": '<h2><a href="https://example.test/">Head<br>line</a></h2>',
    "table-caption": "<table><caption>Elvar<br>Fennick</caption><tr><td>c</td></tr></table>",
    "figure-caption": (
        '<figure><img src="k.png" alt="alt"><figcaption>Jorran<br>Kestrel'
        "</figcaption></figure>"
    ),
    # Nothing but line breaks: what the bridge makes of the element empty.
    # The backend makes no item of an empty list item, so an ordered list
    # numbers the items after it one lower than a browser does, as it does
    # for `<li></li>`; an empty item that holds a list has its own line, the
    # stand-in P45 gives `<li><ul>` (docs/09 P45, P56).
    "item-only": "<ul><li><br></li></ul>",
    "item-only-spaced": "<ul><li>&nbsp;<br> </li></ul>",
    "item-only-numbered": "<ol><li>Raskel</li><li><br></li><li>Sorvin</li></ol>",
    "item-only-numbered-from": (
        '<ol start="5"><li>Raskel</li><li><br></li><li>Sorvin</li></ol>'
    ),
    "item-only-nested-first": "<ul><li>Umber<ul><li><br></li><li>Varrick</li></ul></li></ul>",
    "item-only-parts": "<ul><li><b><br></b><br></li><li>Zennor</li></ul>",
    "item-only-and-a-list": (
        "<ul><li><br><ul><li>Varrick</li></ul></li><li>Wendle</li></ul>"
    ),
    "item-only-and-a-list-nested": (
        "<ul><li>Umber<ul><li><br><ul><li>Varrick</li></ul></li></ul></li></ul>"
    ),
    "item-only-and-an-image": '<ul><li><img src="k.png"><br></li></ul>',
    "item-only-and-a-table": (
        "<ul><li><br><table><tr><td>Hollin</td></tr></table></li></ul>"
    ),
    "task-only": '<ul><li><input type="checkbox"><br></li></ul>',
    "term-only": "<dl><dt><br></dt><dd>Callow</dd></dl>",
    "description-only": "<dl><dt>Abbet</dt><dd><br></dd></dl>",
    "heading-only": "<h2><br></h2><p>body</p>",
    "table-caption-only": (
        "<table><caption><span><br></span></caption><tr><td>c</td></tr></table>"
    ),
    "figure-caption-only": '<figure><img src="k.png"><figcaption><br></figcaption></figure>',
    # The backend captions a figure with its image's alt text when the
    # caption is empty.
    "figure-caption-only-alt": (
        '<figure><img src="k.png" alt="alt"><figcaption><br></figcaption></figure>'
    ),
}

#: In a `<pre>`, each against the same `<pre>` holding a line ending.
IN_CODE = {
    "code": "<pre>first<br>second</pre>",
    "code-blank-line": "<pre>first<br><br>third</pre>",
    "code-edges": "<pre><br>first<br>second<br></pre>",
    "code-only": "<pre><br></pre><p>body</p>",
    "code-parts": "<pre><b>first</b><br>second</pre>",
    # A list item's `<pre>` is a block of code under the item (docs/09 P50).
    "code-in-an-item": "<ul><li>Step<pre>first<br>second</pre></li></ul>",
}

#: Where the page without the `<br>` is not the same page to the backend, so
#: only the character is checked. docling makes a cell holding a `<br>` a rich
#: cell, and an item's `<br>` in a run of its own formatting leaves the item
#: its other parts as an inline group, where a space leaves one plain item
#: (every text surface reads the same).
OTHERS = {
    "cell-item": "<table><tr><td><ul><li>Hollin<br>Ibbet</li></ul></td></tr></table>",
    "cell-code": "<table><tr><td><pre>first<br>second</pre></td><td>z</td></tr></table>",
    "item-own-run": "<ul><li><b>Morvik</b><i><br></i></li></ul>",
}


@pytest.mark.parametrize("body", ONE_LINE.values(), ids=ONE_LINE.keys())
def test_a_line_break_read_onto_one_line_is_a_space(body: str) -> None:
    record = _record(_page(body))
    assert _carrying(record) == []
    assert record == _record(_page(body.replace("<br>", " ")))


@pytest.mark.parametrize("body", IN_CODE.values(), ids=IN_CODE.keys())
def test_a_line_break_in_code_is_a_line_ending(body: str) -> None:
    record = _record(_page(body))
    assert _carrying(record) == []
    assert record == _record(_page(body.replace("<br>", "\n")))


@pytest.mark.parametrize("body", OTHERS.values(), ids=OTHERS.keys())
def test_no_other_shape_carries_the_character(body: str) -> None:
    record = _record(_page(body))
    assert _carrying(record) == []
    assert record["warnings"] == []


def test_the_fixture_reads_as_a_browser_lays_it_out() -> None:
    record = _record(FIXTURE.read_text(encoding="utf-8"))
    assert _carrying(record) == []
    assert record["warnings"] == []
    markdown = record["content.md"]
    for block in (
        "# Line breaks in items",
        # A paragraph's line break is the backend's own, and unchanged.
        "A paragraph keeps its line  \nbreak.",
        "- Halder\n- Jask ov\n- Kel mar\n- Lunden\n- **Morvik** Nalder\n"
        "- [Ostrel Pellin](https://example.test/ostrel)\n- see `quen by` now",
        "1. Raskel\n2. Sorvin Tallow",
        "- Umber\n    - Varrick Wendle",
        "- [x] Yarrow Zennor",
        "- **Abbet Brisk**\n    - Callow Dunmore",
        "## Head line",
        "Elvar Fennick",
        "Jorran Kestrel",
        "```\nfirst\nsecond\n\nfourth\n```",
    ):
        assert block in markdown
    text = record["content.txt"].splitlines()
    for line in ("- Jask ov", "- [x] Yarrow Zennor", "Head line", "fourth"):
        assert line in text
    # A cell's line break is the backend's own, too.
    assert record["tables"] == [[["Galt"], ["Hollin\nIbbet"]]]
    headings = {heading for _, path, _, _ in record["chunks"] for heading in path}
    assert "Head line" in headings


def test_chrome_with_a_line_break_stays_chrome() -> None:
    """The bridge files text from before the first heading back on the body
    unless it reads out of a header, nav, footer or aside. A nav item with a
    `<br>` carried the character, so it read out of none of them and joined
    the body, alone of its list."""
    page = (
        "<html><body><nav><ul><li>Harbour<br>map</li><li>Ledger</li></ul></nav>"
        "<h1>T</h1><p>body</p></body></html>"
    )
    record = _record(page)
    layers = {
        item["text"]: item["content_layer"] for item in record["content.json"]["texts"]
    }
    assert layers["Harbour map"] == layers["Ledger"] == "furniture"
    assert "Harbour" not in record["content.md"]


def test_a_caption_with_a_line_break_is_found_carried() -> None:
    """With more tables in the page than the backend made, the bridge looks
    for each caption among those it carried; one with a `<br>` was not found
    and reported lost although content.md had it."""
    page = _page(
        "<table><caption>Elvar<br>Fennick</caption><tr><td>"
        "<table><tr><td>in</td></tr></table></td></tr></table>"
    )
    record = _record(page)
    assert "Elvar Fennick" in record["content.md"]
    assert record["warnings"] == []


@pytest.mark.parametrize(
    "own",
    [
        LINE_BREAK,
        "&#xe000;",
        "&#XE000;",
        "&#xE000",
        "&#x0e000;",
        "&#57344;",
        "&#057344",
    ],
    ids=[
        "character",
        "hex",
        "upper",
        "no-semicolon",
        "zero",
        "decimal",
        "decimal-bare",
    ],
)
def test_a_page_with_its_own_character_says_so(own: str) -> None:
    """The backend deletes the page's own U+E000 from its text, so it is no
    line break; one an attribute carries into a text is read as one. A
    reference is the character to the backend's parser, so it counts too.
    The page declares its charset, as the snapshot does: undeclared, the
    backend's charset detection reads the character's bytes as Cyrillic."""
    page = (
        '<html><head><meta charset="utf-8"></head><body><h1>T</h1>'
        f'<p>Tam{own}sin</p><figure><img src="k.png" alt="Kes{own}trel"></figure>'
        "</body></html>"
    )
    record = _record(page)
    assert _carrying(record) == []
    assert "Tamsin" in record["content.txt"]
    assert "Kes trel" in record["content.txt"]
    assert any("U+E000" in warning for warning in record["warnings"])


def test_a_reference_to_another_character_is_not_the_page_s_own() -> None:
    record = _record(_page("<p>Tam&#xe0001;sin &#573440; &#xe00;</p>"))
    assert not any("U+E000" in warning for warning in record["warnings"])


def test_one_the_bridge_does_not_reach_is_a_space_and_reported(monkeypatch) -> None:
    """Every text item is written out. A line break the backend leaves in any
    other field is read there as whitespace, and the warning names where."""
    from docling.backend.html_backend import HTMLDocumentBackend

    convert = HTMLDocumentBackend.convert

    def into_a_cell(self: Any) -> Any:
        document = convert(self)
        document.tables[0].data.table_cells[0].text = f"Hollin{LINE_BREAK}Ibbet"
        return document

    monkeypatch.setattr(HTMLDocumentBackend, "convert", into_a_cell)
    record = _record(_page("<table><tr><td>Hollin</td></tr></table>"))
    assert _carrying(record) == []
    assert record["tables"] == [[["Hollin Ibbet"]]]
    (warning,) = record["warnings"]
    assert "(content.json, content.md, content.txt, content.doctags, tables" in warning


def test_removing_items_is_docling_cores_own_removal(monkeypatch) -> None:
    """The bridge removes an item a line break alone made, in one pass over
    the document; docling-core's `delete_items` sums over every removed item
    for each reference left, so 1,000 such items took 2.3 s. The two must
    leave the same document: every reference renumbered, and each one to a
    removed item, a caption's included, dropped. With `drop_lone_line_breaks`
    off, so that list items and captions reach the removal too."""
    from webshot.extract import docling_bridge

    monkeypatch.setattr(docling_bridge, "drop_lone_line_breaks", lambda html: html)
    page = _page(
        "".join(
            ONE_LINE[name]
            for name in (
                "item-only",
                "item-only-numbered",
                "item-only-nested-first",
                "item-only-parts",
                "table-caption-only",
                "figure-caption-only",
                "item-parts",
                "terms",
            )
        )
        + OTHERS["item-own-run"]
        + "<p>Tail <b>bold</b> end</p>"
    )
    removed: list[set[str]] = []
    remove_items = docling_bridge._remove_items

    def counted(document: Any, refs: set[str]) -> None:
        removed.append(set(refs))
        remove_items(document, refs)

    monkeypatch.setattr(docling_bridge, "_remove_items", counted)
    ours = _record(page)
    # Three list items; a list item, its inline group and its two parts;
    # two captions; and a part of an item that keeps its text.
    (refs,) = removed
    assert len(refs) == 10

    def delete_items(document: Any, refs: set[str]) -> None:
        held = [*document.texts, *document.groups]
        document.delete_items(
            node_items=[
                item
                for item in held
                if item.self_ref in refs and item.parent.cref not in refs
            ]
        )

    monkeypatch.setattr(docling_bridge, "_remove_items", delete_items)
    assert _record(page) == ours
    assert "Sorvin" in ours["content.md"]
    # Every reference left points at an item that is there, and back.
    document = ours["content.json"]
    docling_bridge.validate_document_json(json.dumps({**document, "origin": None}))
    for label in ("texts", "groups"):
        for index, item in enumerate(document[label]):
            assert item["self_ref"] == f"#/{label}/{index}"
            for child in item["children"]:
                _, held, at = child["$ref"].split("/")
                assert document[held][int(at)]["parent"]["$ref"] == item["self_ref"]


@pytest.mark.parametrize(
    ("text", "lines", "written"),
    [
        ("plain", False, "plain"),
        (f"a {LINE_BREAK} b", False, "a b"),
        (f"a {LINE_BREAK} {LINE_BREAK} b", False, "a b"),
        (f"{LINE_BREAK} a {LINE_BREAK}", False, "a"),
        (f"do{LINE_BREAK}it", False, "do it"),
        (LINE_BREAK, False, ""),
        (f"a {LINE_BREAK} b", True, "a\nb"),
        (f"a {LINE_BREAK} {LINE_BREAK} b", True, "a\n\nb"),
        (f"{LINE_BREAK} a {LINE_BREAK}", True, "a"),
        # Only the backend's joining spaces go: the code's own stay.
        (f"a\n{LINE_BREAK} b", True, "a\n\nb"),
        (f"a  {LINE_BREAK}  b", True, "a\nb"),
    ],
)
def test_write_line_breaks(text: str, lines: bool, written: str) -> None:
    assert write_line_breaks(text, lines=lines) == written


@pytest.mark.parametrize(
    ("html", "dropped"),
    [
        ("<ul><li>Jask<br>ov</li></ul>", "<ul><li>Jask<br>ov</li></ul>"),
        ("<ul><li><br></li></ul>", "<ul><li></li></ul>"),
        ("<ul><li><b><br/></b> <br /></li></ul>", "<ul><li><b></b> </li></ul>"),
        # The page's own U+E000 is no text: the backend deletes it.
        ("<ul><li>&#xe000;<br></li></ul>", "<ul><li>&#xe000;</li></ul>"),
        # An item's lists and tables are not its own text, and a line break
        # in one is that list's or cell's.
        (
            "<ul><li><br><ul><li>Va<br>rick</li></ul></li></ul>",
            "<ul><li><ul><li>Va<br>rick</li></ul></li></ul>",
        ),
        (
            "<ul><li><table><tr><td>a<br>b</td></tr></table><br></li></ul>",
            "<ul><li><table><tr><td>a<br>b</td></tr></table></li></ul>",
        ),
        # An image is no text either.
        (
            '<ul><li><img src="k.png"><br></li></ul>',
            '<ul><li><img src="k.png"></li></ul>',
        ),
        ("<dl><dt><br></dt><dd>d<br></dd></dl>", "<dl><dt></dt><dd>d<br></dd></dl>"),
        (
            "<figure><figcaption><br></figcaption></figure>",
            "<figure><figcaption></figcaption></figure>",
        ),
        # A caption's text is all of it, a list's included.
        (
            "<table><caption><ul><li>x</li></ul><br></caption></table>",
            "<table><caption><ul><li>x</li></ul><br></caption></table>",
        ),
        # Not an item, which the backend reads as a paragraph.
        ("<p><br></p><div><li><br></li></div>", "<p><br></p><div><li><br></li></div>"),
    ],
)
def test_drop_lone_line_breaks(html: str, dropped: str) -> None:
    assert drop_lone_line_breaks(html) == dropped


def test_a_page_with_no_line_break_is_parsed_as_given() -> None:
    page = _page("<ul><li></li></ul>")
    assert drop_lone_line_breaks(page) is page
