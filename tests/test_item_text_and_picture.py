"""A list item with text keeps its picture or table inside it (docs/09 P61).

docling writes what an item holds after the item's line, the first line at the
item's indent and the rest at the margin. A CommonMark reader then read a
picture's caption as more of the item's text, the `<!-- image -->` as the end
of the list, and a table as a paragraph of pipes in the item. P52 put an empty
item's picture or table inside it; this is the same for an item with text.

`item_text_and_picture.html` holds each shape. As in P45's and P52's tests,
content.md is read back with markdown-it-py, a CommonMark reader (here with GFM
tables), and its lists are compared with the fixture's own, item by item.
"""

from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import pytest
from markdown_it import MarkdownIt

from webshot.capture.visuals import VisualAsset
from webshot.extract import docling_bridge
from webshot.extract.docling_bridge import EMPTY_ITEM, extract
from webshot.ocr.tesseract import OCRResult

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "item_text_and_picture.html"

#: A cell's list is written on one line, so it is not read back as a list.
NOT_READ_BACK = {"In a table"}

#: A list as (tag, start, items); an item as [what it shows, the lists it holds].
List = tuple[str, str, list[Any]]


class _Lists(HTMLParser):
    """Each top-level `<ul>`/`<ol>` between `<h2>` and the next, as a `List`.

    An item shows its text, then a picture as its alt text and `[image]`, and a
    table as `[table`, its cells' text and `]`. Read from content.md rendered,
    a picture is the caption docling writes and the `<!-- image -->` after it.
    """

    def __init__(self) -> None:
        super().__init__()
        self.sections: dict[str, list[List]] = {}
        self._section = ""
        self._heading: list[str] | None = None
        self._open: list[Any] = []
        self._cells: list[str] | None = None

    def _show(self, text: str) -> None:
        if self._open and isinstance(self._open[-1], list) and text.strip():
            self._open[-1][0] = " ".join(f"{self._open[-1][0]} {text}".split())

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "h2":
            self._heading = []
        elif tag == "table" and self._open:
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
        if tag == "h2" and self._heading is not None:
            self._section = "".join(self._heading).strip()
            self._heading = None
        elif tag == "table" and self._cells is not None:
            cells, self._cells = self._cells, None
            self._show(f"[table {' '.join(cells)}]")
        elif tag in {"ul", "ol", "li"} and self._open:
            self._open.pop()

    def handle_comment(self, data: str) -> None:
        if data.strip() == "image":
            self._show("[image]")

    def handle_data(self, data: str) -> None:
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
            text=f"GLYPH{number}\n\nsecond line",
            confidence=90.0,
            language="eng",
            engine="Tesseract",
        ),
        sha256="ab" * 32,
    )


def _docling_s_own(base: type) -> type:
    """`base` as docling writes it, taking `_holding`'s field and nothing else."""

    class _Own(base):
        holders: frozenset[str] = frozenset()

    return _Own


@pytest.fixture(scope="module")
def result() -> Any:
    return extract(FIXTURE.read_text("utf-8"), [])


@pytest.fixture(scope="module")
def recognized() -> Any:
    return extract(FIXTURE.read_text("utf-8"), [_asset(n) for n in range(1, 11)])


def test_content_md_holds_every_list_as_the_page_does(
    result: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every list reads back from content.md with a CommonMark reader as the
    page's own, each picture and table inside its item and one list where the
    page has one. Written as docling writes them, none of the nine does."""
    expected = _sections(FIXTURE.read_text("utf-8"))
    assert len(expected) == 9
    got = _sections(_reader().render(result.markdown))
    assert list(got) == list(expected)
    for section, lists in expected.items():
        assert got[section] == lists, section

    monkeypatch.setattr(docling_bridge, "_holding", _docling_s_own)
    own = _sections(_reader().render(extract(FIXTURE.read_text("utf-8"), []).markdown))
    assert [name for name in expected if own.get(name) == expected[name]] == []


@pytest.mark.parametrize(
    ("markdown", "text"),
    [
        pytest.param(
            "- Halder\n\n  Corvel chart\n\n  <!-- image -->\n- Istrel",
            "- Halder\n\n  Corvel chart\n- Istrel",
            id="picture",
        ),
        pytest.param(
            "- Jaskov\n\n  <!-- image -->\n- Kelmar", "- Jaskov\n- Kelmar", id="no-alt"
        ),
        pytest.param(
            "- Lunden\n\n  | Cellvane | Dorn |\n  | - | - |\n  | 1 | 2 |\n- Morvik",
            None,
            id="table",
        ),
        pytest.param(
            "- Nalder\n\n  Ostrel map\n\n  <!-- image -->\n    - Pellin\n- Quenby",
            "- Nalder\n\n  Ostrel map\n    - Pellin\n- Quenby",
            id="picture-and-list",
        ),
        pytest.param(
            "- Raskel\n\n  | Sorvin |\n  | - |\n\n  Tallow plan\n\n  <!-- image -->\n"
            "- Umber",
            "- Raskel\n\n  | Sorvin |\n  | - |\n\n  Tallow plan\n- Umber",
            id="table-then-picture",
        ),
        pytest.param(
            "- Varrick\n    - Wendle\n\n      Yarrow sketch\n\n      <!-- image -->\n"
            "    - Zennor",
            "- Varrick\n    - Wendle\n\n      Yarrow sketch\n    - Zennor",
            id="under-text",
        ),
        pytest.param(
            "9. Abbet\n10. Brisk\n\n    Callow chart\n\n    <!-- image -->\n"
            "11. Dunmore",
            "9. Abbet\n10. Brisk\n\n    Callow chart\n11. Dunmore",
            id="from-nine",
        ),
        pytest.param(
            "- Elvar **Fennick**\n\n  Galt\n\n  <!-- image -->\n\n  Harbour\n\n"
            "  <!-- image -->\n- Ingle",
            "- Elvar Fennick\n\n  Galt\n\n  Harbour\n- Ingle",
            id="two-pictures",
        ),
        pytest.param(
            "-   - Jorvel\n\n    Kestrel\n\n    <!-- image -->\n- Larch",
            "-   - Jorvel\n\n    Kestrel\n- Larch",
            id="empty-item-list-then-picture",
        ),
    ],
)
def test_each_text_surface_writes_the_block_inside_its_item(
    result: Any, markdown: str, text: str | None
) -> None:
    """A picture or a table goes after a blank line, every line of it indented
    to where the item's content begins: two columns past `-`, four past `10.`.
    content.txt and the chunks write a picture as its caption alone, so a
    picture with neither caption nor OCR text adds nothing there. In an empty
    item, a picture after a list is the item's, not the list's last item's."""
    text = markdown if text is None else text
    chunk_text = text.replace("Elvar Fennick", "Elvar **Fennick**")
    chunks = [seed.text for seed in result.chunk_seeds]
    assert f"\n\n{markdown}\n\n" in result.markdown
    assert f"\n\n{text}\n\n" in result.text
    assert chunk_text in chunks


def test_docling_s_own_layout_would_not_do() -> None:
    """Why the blank line and the indent. At the margin, a caption runs into
    the item's text, the `<!-- image -->` ends the list, and a table is a
    paragraph of pipes. Indented without a blank line, the caption is still
    the item's text. Indented to the column of the item's nested lists rather
    than its content, a picture under `100.` leaves the list."""
    reader = _reader()
    margin = reader.render("- Halder\nCorvel chart\n\n<!-- image -->\n- Istrel\n")
    assert margin.startswith(
        "<ul>\n<li>Halder\nCorvel chart</li>\n</ul>\n<!-- image -->"
    )
    pipes = reader.render("- Lunden\n| Cellvane | Dorn |\n| - | - |\n- Morvik\n")
    assert "<table>" not in pipes
    no_gap = reader.render("- Halder\n  Corvel chart\n\n  <!-- image -->\n- Istrel\n")
    assert no_gap.startswith("<ul>\n<li>\n<p>Halder\nCorvel chart</p>")
    wide = reader.render("99. Abbet\n100. Brisk\n\n    Callow chart\n101. Dunmore\n")
    assert wide.count("<ol") == 2
    assert reader.render(
        "- Halder\n\n  Corvel chart\n\n  <!-- image -->\n- Istrel\n"
    ) == (
        "<ul>\n<li>\n<p>Halder</p>\n<p>Corvel chart</p>\n<!-- image -->\n</li>\n"
        "<li>\n<p>Istrel</p>\n</li>\n</ul>\n"
    )


@pytest.mark.parametrize(
    ("page", "lines"),
    [
        pytest.param(
            "<ol>{}<li>Brisk <img src='a.png' alt='Callow'></li><li>Dunmore</li></ol>".format(
                "".join(f"<li>Abbet {n}</li>" for n in range(1, 100))
            ),
            "100. Brisk\n\n     Callow\n\n     <!-- image -->\n101. Dunmore",
            id="hundred",
        ),
        pytest.param(
            "<ol>{}<li><img src='a.png' alt='Callow'></li><li>Dunmore</li></ol>".format(
                "".join(f"<li>Abbet {n}</li>" for n in range(1, 100))
            ),
            "100. Callow\n\n     <!-- image -->\n101. Dunmore",
            id="hundred-empty",
        ),
        pytest.param(
            "<ul><li><pre>x = 1</pre><img src='a.png' alt='Callow'></li></ul>",
            "-   ```\n    x = 1\n    ```\n\n    Callow\n\n    <!-- image -->",
            id="empty-item-opening-with-code",
        ),
        pytest.param(
            "<ul><li>Galt</li><li><img src='a.png' alt='Callow'><pre>x = 1</pre></li></ul>",
            "- Galt\n-   Callow\n\n    <!-- image -->\n    ```\n    x = 1\n    ```",
            id="item-with-code-opening-with-a-picture",
        ),
        pytest.param(
            "<ul><li><table><tr><td>Sorvin</td></tr></table><pre>x = 1</pre>"
            "<img src='a.png' alt='Callow'></li></ul>",
            "-   | Sorvin |\n    | - |\n    ```\n    x = 1\n    ```\n\n    Callow\n\n"
            "    <!-- image -->",
            id="item-with-code-and-two-blocks",
        ),
        pytest.param(
            "<ol start='10'><li>Varrick<ol><li>Brisk <img src='a.png' alt='Callow'>"
            "</li></ol></li></ol>",
            "10. Varrick\n    1. Brisk\n\n       Callow\n\n       <!-- image -->",
            id="nested-under-ten",
        ),
        pytest.param(
            "<ul><li>Halder <img src='a.png' alt='Corvel'><ol start='5'><li>Pellin</li>"
            "</ol></li></ul>",
            "- Halder\n\n  Corvel\n\n  <!-- image -->\n    5. Pellin",
            id="numbered-list-after",
        ),
    ],
)
def test_the_content_column_follows_the_marker(page: str, lines: str) -> None:
    """The column is the item's own: past its marker and one space, at any
    depth and any width of number, so it reads back inside the item. An empty
    item's marker is padded to four columns (P52), and a wider one, which
    cannot be, has its picture one space past it rather than outside it. An
    empty item that opens with its code (P50) has its content past the
    padding after its marker, so its picture goes there. An item with no text
    that holds code but opens with a picture or a table is docling's, written
    as a marker alone, so its first block goes on that line, as an empty
    item's does, and its others in the same column."""
    result = extract(f"<html><body><h1>T</h1>{page}</body></html>", [])
    assert f"\n{lines}\n" in result.markdown
    tokens = _reader().parse(result.markdown)
    depth, inside, outside = 0, 0, 0
    for token in tokens:
        depth += {"list_item_open": 1, "list_item_close": -1}.get(token.type, 0)
        if token.type == "html_block" and "<!-- image -->" in token.content:
            inside, outside = (inside + 1, outside) if depth else (inside, outside + 1)
    assert (inside, outside) == (1, 0)
    lists = sum(t.type in {"bullet_list_open", "ordered_list_open"} for t in tokens)
    assert lists == page.count("<ol") + page.count("<ul")


def test_ocr_text_is_the_item_s(recognized: Any) -> None:
    """An asset's recognized text, which docling writes between the caption and
    the `<!-- image -->`, is indented with them, blank lines and all, so it
    reads as the item's. Every picture is matched to its asset."""
    assert recognized.warnings == []
    assert recognized.pictured_asset_ids == [f"asset-{n:03d}" for n in range(1, 11)]
    block = (
        "- Halder\n\n  Corvel chart\n\n"
        "  [OCR text from asset-001, machine-recognized by Tesseract, mean confidence "
        "90%:]\n  GLYPH1\n\n  second line\n  [End of OCR text from asset-001]"
    )
    assert f"\n\n{block}\n\n  <!-- image -->\n- Istrel\n\n" in recognized.markdown
    assert f"\n\n{block}\n- Istrel\n\n" in recognized.text
    assert f"{block}\n- Istrel" in [seed.text for seed in recognized.chunk_seeds]
    rendered = _reader().render(recognized.markdown)
    assert (
        "<li>\n<p>Halder</p>\n<p>Corvel chart</p>\n<p>[OCR text from asset-001, "
        "machine-recognized by Tesseract, mean confidence 90%:]\nGLYPH1</p>\n"
        "<p>second line\n[End of OCR text from asset-001]</p>\n<!-- image -->\n</li>"
    ) in rendered
    # The figure's own chunk is written at the margin, as before.
    figure = next(s for s in recognized.chunk_seeds if s.doc_items == ("#/pictures/0",))
    assert "\n[OCR text from asset-001" in figure.text


def test_only_indents_and_blank_lines_change(
    result: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Against docling's own layout, with no empty item on the page, each text
    surface differs only in indentation and blank lines, and content.json, the
    doctags, the tables and the chunks' items not at all."""
    monkeypatch.setattr(docling_bridge, "mark_empty_items", lambda html: html)
    page = FIXTURE.read_text("utf-8")
    ours = extract(page, [])
    monkeypatch.setattr(docling_bridge, "_holding", _docling_s_own)
    own = extract(page, [])

    def words(surface: str) -> list[str]:
        return [line.strip() for line in surface.split("\n") if line.strip()]

    assert words(ours.markdown) == words(own.markdown)
    assert words(ours.text) == words(own.text)
    assert [words(s.text) for s in ours.chunk_seeds] == [
        words(s.text) for s in own.chunk_seeds
    ]
    assert [s.doc_items for s in ours.chunk_seeds] == [
        s.doc_items for s in own.chunk_seeds
    ]
    assert ours.document_json == own.document_json
    assert ours.doctags == own.doctags
    assert ours.tables == own.tables
    assert ours.markdown != own.markdown


def test_no_line_is_left_with_only_spaces(result: Any, recognized: Any) -> None:
    """The blank line before a block ends the part before it, so the list
    serializer's indent never lands on it, and no stand-in or byte-order mark
    reaches any surface."""
    for extracted in (result, recognized):
        surfaces = [extracted.markdown, extracted.text]
        surfaces += [seed.text for seed in extracted.chunk_seeds]
        for surface in surfaces:
            assert all(line == line.rstrip() for line in surface.split("\n"))
            assert "\n\n\n" not in surface
            assert EMPTY_ITEM not in surface
            assert "\N{ZERO WIDTH NO-BREAK SPACE}" not in surface


def test_a_table_cell_is_left_as_docling_writes_it(result: Any) -> None:
    """docling writes a cell's list on one line; nothing is indented there."""
    assert "| - Mallow Norrin  <!-- image --> - Oskar |" in result.markdown
    assert "| - Mallow Norrin - Oskar |" in result.text
