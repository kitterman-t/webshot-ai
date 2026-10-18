"""A list item whose only content is a picture or a table keeps it (docs/09 P52).

docling's HTML backend makes no item of an `<li>` with no text of its own, and
only looks for lists in one, so a picture or a table that was all an item held
was dropped from content.json, content.md, content.txt and the chunks, with no
warning. A dropped picture also left the bridge one picture short of the
snapshot's images, so every OCR annotation on the page was omitted.

`item_holding_a_picture.html` holds each shape. As in P45's tests, content.md
is read back with markdown-it-py, a CommonMark reader (here with GFM tables),
and its lists are compared with the fixture's own, item by item.
"""

from __future__ import annotations

import difflib
import json
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import pytest
from markdown_it import MarkdownIt

from webshot.capture.visuals import VisualAsset
from webshot.extract import docling_bridge
from webshot.extract.docling_bridge import EMPTY_ITEM, extract, mark_empty_items
from webshot.ocr.tesseract import OCRResult

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "item_holding_a_picture.html"

#: The sections whose lists are not read back: a cell's list is written on
#: one line, and docling writes an item with text and a picture its own way.
NOT_READ_BACK = {"In a table", "With text"}

#: A list as (tag, start, items); an item as (what it shows, the lists it holds).
List = tuple[str, str, list[Any]]


class _Lists(HTMLParser):
    """Each top-level `<ul>`/`<ol>` between `<h2>` and the next, as a `List`.

    An item shows its text, a picture as its alt text and `[image]`, and a
    table as `[table` and its cells' text `]`. Read from the fixture, a picture
    is an `<img>`; read from content.md rendered, it is the caption docling
    writes and the `<!-- image -->` after it.
    """

    def __init__(self) -> None:
        super().__init__()
        self.sections: dict[str, list[List]] = {}
        self._section = ""
        self._heading: list[str] | None = None
        self._open: list[Any] = []
        self._skip = 0
        self._cells: list[str] | None = None

    def _show(self, text: str) -> None:
        if self._open and isinstance(self._open[-1], list) and text.strip():
            self._open[-1][0] = " ".join(f"{self._open[-1][0]} {text}".split())

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "nav" or (tag == "table" and not self._open):
            self._skip += 1
        elif self._skip:
            return
        elif tag == "h2":
            self._heading = []
        elif tag == "table":
            self._cells = []
        elif tag == "img":
            self._show(f"{dict(attrs).get('alt') or ''} [image]")
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
        if tag == "nav" or (tag == "table" and self._skip and self._cells is None):
            self._skip -= 1
        elif self._skip:
            return
        elif tag == "h2" and self._heading is not None:
            self._section = "".join(self._heading).strip()
            self._heading = None
        elif tag == "table" and self._cells is not None:
            cells, self._cells = self._cells, None
            self._show(f"[table {' '.join(cells)}]")
        elif tag in {"ul", "ol", "li"}:
            self._open.pop()

    def handle_comment(self, data: str) -> None:
        if not self._skip and data.strip() == "image":
            self._show("[image]")

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        if self._heading is not None:
            self._heading.append(data)
        elif self._cells is not None:
            self._cells.extend(data.split())
        else:
            self._show(data)


def _sections(html: str) -> dict[str, list[List]]:
    parser = _Lists()
    parser.feed(html)
    for name in NOT_READ_BACK:
        parser.sections.pop(name, None)
    return parser.sections


def _reader() -> MarkdownIt:
    return MarkdownIt("commonmark").enable("table")


def _asset(number: int) -> VisualAsset:
    asset_id = f"asset-{number:03d}"
    return VisualAsset(
        id=asset_id,
        file=f"assets/{asset_id}.png",
        kind="image",
        width=640,
        height=240,
        ocr=OCRResult(
            text=f"GLYPH{number}", confidence=90.0, language="eng", engine="Tesseract"
        ),
        sha256="ab" * 32,
    )


@pytest.fixture(scope="module")
def result() -> Any:
    return extract(FIXTURE.read_text("utf-8"), [])


@pytest.fixture(scope="module")
def recognized() -> Any:
    return extract(FIXTURE.read_text("utf-8"), [_asset(n) for n in range(1, 9)])


def test_content_md_holds_every_list_as_the_page_does(result: Any) -> None:
    """Every list reads back from content.md with a CommonMark reader as the
    page's own, each picture and table inside its item. Before the fix every
    one of these items was missing, its picture or table with it."""
    expected = _sections(FIXTURE.read_text("utf-8"))
    rendered = _reader().render(result.markdown)
    got = _sections(rendered)
    assert list(got) == list(expected)
    for section, lists in expected.items():
        assert got[section] == lists, section
    assert "<pre>" not in rendered


@pytest.mark.parametrize(
    ("markdown", "text"),
    [
        pytest.param(
            "-   Corvel chart\n\n    <!-- image -->\n- Istrel",
            "-   Corvel chart\n- Istrel",
            id="picture",
        ),
        pytest.param("-   <!-- image -->\n- Jaskov", "-\n- Jaskov", id="no-alt"),
        pytest.param(
            "-   | Cellvane | Dorn |\n    | - | - |\n    | 1 | 2 |\n- Kelmar",
            None,
            id="table",
        ),
        pytest.param(
            "-   Lunden map\n\n    <!-- image -->\n    - Morvik\n- Nalder",
            "-   Lunden map\n    - Morvik\n- Nalder",
            id="picture-and-list",
        ),
        pytest.param(
            "- Ostrel\n    -   Pellin plan\n\n        <!-- image -->\n    - Quenby",
            "- Ostrel\n    -   Pellin plan\n    - Quenby",
            id="under-text",
        ),
        pytest.param(
            "5. Raskel\n6.  | Sorvin |\n    | - |\n7. Tallow", None, id="from-five"
        ),
        pytest.param(
            "-   Umber sketch\n\n    <!-- image -->\n- Varrick",
            "-   Umber sketch\n- Varrick",
            id="figure",
        ),
        pytest.param("-   - Wendle\n- Yarrow", None, id="video-and-list"),
    ],
)
def test_each_text_surface_writes_what_the_item_holds_inside_it(
    result: Any, markdown: str, text: str | None
) -> None:
    """The picture or table goes on the item's line and its other lines are
    indented to the item's content, as CommonMark nests a block in an item.
    content.txt and the chunks write a picture as its caption alone, with no
    `<!-- image -->`, so a picture with no alt leaves the item's marker bare."""
    text = markdown if text is None else text
    chunks = [seed.text for seed in result.chunk_seeds]
    assert f"\n\n{markdown}\n\n" in result.markdown
    assert f"\n\n{text}\n\n" in result.text
    assert text in chunks


def test_docling_s_own_layout_would_not_do() -> None:
    """Why the content is indented: written as docling writes what an item with
    text holds, at the left margin, a caption ran into the item's text, the
    `<!-- image -->` ended the list, and a table became a paragraph of pipes.
    With the marker on a line of its own, the picture or table followed an
    empty item, outside it."""
    reader = _reader()
    flush = reader.render("-\nCorvel chart\n\n<!-- image -->\n- Istrel\n")
    assert flush.startswith("<ul>\n<li></li>\n</ul>\n<p>Corvel chart</p>")
    joined = reader.render("- | Cellvane | Dorn |\n| - | - |\n| 1 | 2 |\n- Kelmar\n")
    assert "<table>" not in joined
    indented = reader.render("-   | Cellvane | Dorn |\n    | - | - |\n- Kelmar\n")
    assert indented.startswith("<ul>\n<li>\n<table>")
    assert reader.render("-   Corvel chart\n\n    <!-- image -->\n- Istrel\n") == (
        "<ul>\n<li>\n<p>Corvel chart</p>\n<!-- image -->\n</li>\n"
        "<li>\n<p>Istrel</p>\n</li>\n</ul>\n"
    )


def test_content_json_holds_the_item_and_what_it_holds(result: Any) -> None:
    """The record holds the `<li>` the page has: an item with no text whose
    child is its picture, table or list, numbered among its siblings. Before,
    the picture and the table were not in it at all, and "Tallow" was 6."""
    document = json.loads(result.document_json)
    by_ref = {item["self_ref"]: item for item in document["texts"]}
    for kind in ("groups", "pictures", "tables"):
        by_ref.update({item["self_ref"]: item for item in document[kind]})
    empty = [
        item
        for item in document["texts"]
        if item["label"] == "list_item" and not item["text"]
    ]
    held = [[by_ref[child["$ref"]]["label"] for child in i["children"]] for i in empty]
    assert held == [
        ["picture"],  # the nav's
        ["picture"],
        ["picture"],
        ["table"],
        ["picture", "list"],
        ["picture"],
        ["table"],
        ["picture"],
        ["list"],  # the video's, which docling makes nothing of
        ["picture"],  # in the table cell
    ]
    assert all(item["orig"] == "" for item in empty)
    markers = [
        item["marker"]
        for item in document["texts"]
        if item["label"] == "list_item" and item["marker"]
    ]
    assert markers == ["5.", "6.", "7."]
    assert len(document["pictures"]) == 8
    assert len(document["tables"]) == 3
    docling_bridge.validate_document_json(result.document_json)


def test_every_picture_keeps_its_ocr_text_inside_its_item(recognized: Any) -> None:
    """With each picture made, the backend's pictures and the snapshot's images
    agree, so every asset's recognized text is attached. Before, the seven
    dropped pictures left one picture for eight images, and the bridge omitted
    all eight annotations with a warning. An item's OCR text is the item's.
    The nav's picture is chrome, so its asset is listed as one (docs/09 P57)."""
    assert recognized.warnings == []
    assert recognized.pictured_asset_ids == [f"asset-{n:03d}" for n in range(2, 9)]
    assert recognized.chrome_pictured_asset_ids == ["asset-001"]
    assert (
        "\n\n-   Corvel chart\n\n"
        "    [OCR text from asset-002, machine-recognized by Tesseract, "
        "mean confidence 90%:]\n    GLYPH2\n    [End of OCR text from asset-002]\n\n"
        "    <!-- image -->\n- Istrel\n\n"
    ) in recognized.markdown
    rendered = _reader().render(recognized.markdown)
    assert (
        "<li>\n<p>Corvel chart</p>\n<p>[OCR text from asset-002, machine-recognized "
        "by Tesseract, mean confidence 90%:]\nGLYPH2\n[End of OCR text from "
        "asset-002]</p>\n<!-- image -->\n</li>"
    ) in rendered
    figures = [seed for seed in recognized.chunk_seeds if seed.kind == "figure"]
    assert len(figures) == 8


def test_a_nav_picture_stays_out_of_the_body(result: Any) -> None:
    """An empty item goes where what it holds goes: the nav's picture is chrome,
    so its item is too, and content.md does not open with a bare marker."""
    document = json.loads(result.document_json)
    nav_item = document["texts"][0]
    assert (nav_item["label"], nav_item["text"]) == ("list_item", "")
    assert nav_item["content_layer"] == "furniture"
    assert document["pictures"][0]["content_layer"] == "furniture"
    assert "Harbour" not in result.markdown
    assert result.markdown.startswith("# Items that hold")


def test_a_table_cell_writes_the_picture_after_the_items_marker(result: Any) -> None:
    """docling writes a cell's list on one line; nothing is indented there, and
    the cell's own text has no space where the stand-in was."""
    assert "| - Zennor  <!-- image --> - Abbet |" in result.markdown
    assert result.tables[2] == [["Steps"], ["Abbet"]]


def test_no_stand_in_reaches_the_record(result: Any, recognized: Any) -> None:
    for extracted in (result, recognized):
        surfaces = [
            extracted.document_json,
            extracted.markdown,
            extracted.text,
            extracted.doctags,
        ]
        surfaces += [seed.text for seed in extracted.chunk_seeds]
        surfaces += [cell for grid in extracted.tables for row in grid for cell in row]
        for surface in surfaces:
            assert EMPTY_ITEM not in surface
            assert "﻿" not in surface
    assert result.warnings == []


def test_only_the_items_lines_change(result: Any, monkeypatch: Any) -> None:
    """Against docling's own reading (the page unmarked), content.md gains the
    items' lines and changes three others: the video's item takes its list's
    first line, "Tallow" is numbered 7 as on the page, and the table cell holds
    its picture. Every other line, the item with text and a picture included,
    is docling's."""
    monkeypatch.setattr(docling_bridge, "mark_empty_items", lambda html: html)
    unmarked = extract(FIXTURE.read_text("utf-8"), []).markdown.split("\n")
    marked = result.markdown.split("\n")
    replaced: list[tuple[list[str], list[str]]] = []
    inserted: list[str] = []
    matcher = difflib.SequenceMatcher(a=unmarked, b=marked, autojunk=False)
    for tag, a0, a1, b0, b1 in matcher.get_opcodes():
        if tag == "insert":
            inserted += marked[b0:b1]
        elif tag != "equal":
            replaced.append((unmarked[a0:a1], marked[b0:b1]))
    assert replaced == [
        (["6. Tallow"], ["6.  | Sorvin |", "    | - |", "7. Tallow"]),
        (["    - Wendle"], ["-   - Wendle"]),
        (["| Abbet |"], ["| - Zennor  <!-- image --> - Abbet |"]),
    ]
    assert inserted == [
        "-   Corvel chart",
        "",
        "    <!-- image -->",
        "-   <!-- image -->",
        "-   | Cellvane | Dorn |",
        "    | - | - |",
        "    | 1 | 2 |",
        "-   Lunden map",
        "",
        "    <!-- image -->",
        "    -   Pellin plan",
        "",
        "        <!-- image -->",
        "-   Umber sketch",
        "",
        "    <!-- image -->",
    ]


@pytest.mark.parametrize(
    "item",
    [
        pytest.param("<li><img src='a.png' alt='a'></li>", id="picture"),
        pytest.param("<li><a href='x'><img src='a.png'></a></li>", id="linked"),
        pytest.param("<li> <p><img src='a.png'></p> </li>", id="paragraph"),
        pytest.param("<li><figure><img src='a.png'></figure></li>", id="figure"),
        pytest.param(
            "<li><figure><img src='a.png'><figcaption> </figcaption></figure></li>",
            id="blank-caption",
        ),
        pytest.param("<li><table><tr><td>a</td></tr></table></li>", id="table"),
        pytest.param("<li><img src='a.png'><ul><li>b</li></ul></li>", id="and-list"),
        pytest.param(
            "<li><video src='v.mp4'><source src='v.webm'></video><ul><li>b</li></ul></li>",
            id="video-and-list",
        ),
        pytest.param(
            "<li><audio src='a.mp3'></audio><img src='a.png'></li>", id="audio-picture"
        ),
        pytest.param("<li><hr><img src='a.png'></li>", id="rule"),
    ],
)
def test_an_item_holding_only_a_picture_or_a_table_is_marked(item: str) -> None:
    marked = mark_empty_items(f"<ul>{item}</ul>")
    assert marked.startswith("﻿")
    assert marked.count(EMPTY_ITEM) == 1
    assert marked.index(EMPTY_ITEM) == len("﻿<ul><li>")


@pytest.mark.parametrize(
    "item",
    [
        pytest.param("<li>a <img src='a.png'></li>", id="text"),
        pytest.param(
            "<li><figure><img src='a.png'><figcaption>a</figcaption></figure></li>",
            id="caption",
        ),
        pytest.param("<li><input type='checkbox'><img src='a.png'></li>", id="box"),
        pytest.param("<li><pre>a</pre><img src='a.png'></li>", id="code"),
        pytest.param("<li><video src='v.mp4'></video></li>", id="video-alone"),
        pytest.param("<li><video>a</video><img src='a.png'></li>", id="video-text"),
    ],
)
def test_an_item_with_anything_else_is_left_to_docling(item: str) -> None:
    """docling's own reading of these is kept: text beside the picture, a
    figure's caption (which docling reads as the item's text), a checkbox
    (which docling writes as an item of its own), a code block, and a video with
    nothing else, which docling makes nothing of wherever it stands."""
    page = f"<ul>{item}</ul>"
    assert mark_empty_items(page) is page


@pytest.mark.parametrize(
    ("character", "content"),
    [
        ("﷒", "<img src='a.png' alt='Corvel'>"),
        ("&#xFDD2;", "<table><tr><td>Cellvane</td></tr></table>"),
    ],
)
def test_a_page_carrying_the_stand_in_says_what_it_left_out(
    character: str, content: str
) -> None:
    """A page with U+FDD2 of its own is not marked, so docling drops such an
    item's picture or table again, and the warning says so."""
    page = (
        '<html><head><meta charset="utf-8"></head><body><h1>T</h1>'
        f"<p>odd {character} kept</p><ul><li>{content}</li><li>a</li></ul></body></html>"
    )
    result = extract(page, [])
    assert "Corvel" not in result.markdown and "Cellvane" not in result.markdown
    warning = next(w for w in result.warnings if "U+FDD2" in w)
    assert "a picture or a table that is all an item holds" in warning
