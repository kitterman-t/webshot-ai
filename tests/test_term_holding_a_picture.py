"""A description-list term keeps the picture, table or list it holds (docs/09 P60).

docling's HTML backend reads a term (`<dt>`) for its text alone and, unlike a
list item or a description, never visits what the term holds, so a picture, a
table or a list in a term was dropped from content.json, content.md,
content.txt and the chunks, with no warning. A term holding nothing else made
no item at all, so its descriptions hung off the list as terms of their own,
and a description holding a description list was dropped with it. A dropped
picture also left the bridge short of the snapshot's images, so every OCR
annotation on the page was omitted.

`term_holding_a_picture.html` holds each shape. content.md is read back with
markdown-it-py, a CommonMark reader (here with GFM tables), as in P45's and
P52's tests.
"""

from __future__ import annotations

import difflib
import json
from pathlib import Path
from typing import Any

import pytest
from markdown_it import MarkdownIt

from webshot.capture.visuals import VisualAsset
from webshot.extract import docling_bridge
from webshot.extract.docling_bridge import EMPTY_ITEM, extract, mark_empty_items
from webshot.ocr.tesseract import OCRResult

FIXTURES = Path(__file__).resolve().parent / "fixtures"
FIXTURE = FIXTURES / "term_holding_a_picture.html"
#: The pictures in the fixture, in document order.
PICTURES = 14


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
        sha256="cd" * 32,
    )


def _unfixed(monkeypatch: Any) -> None:
    """docling's own reading: the page unmarked, its terms read for text alone,
    and a description holding a description list read for that list alone
    (P73), which drops it with no term to hang it off."""
    monkeypatch.setattr(docling_bridge, "mark_empty_items", lambda html: html)
    monkeypatch.setattr(docling_bridge, "_reading_terms", lambda base: base)
    monkeypatch.setattr(docling_bridge, "_reading_descriptions", lambda base: base)


def _section(markdown: str, heading: str) -> str:
    """The lines under `## heading` in `markdown`, up to the next heading."""
    after = markdown.split(f"\n## {heading}\n\n", 1)[1]
    return after.split("\n## ", 1)[0].strip("\n")


@pytest.fixture(scope="module")
def result() -> Any:
    return extract(FIXTURE.read_text("utf-8"), [])


@pytest.fixture(scope="module")
def recognized() -> Any:
    return extract(
        FIXTURE.read_text("utf-8"), [_asset(n) for n in range(1, PICTURES + 1)]
    )


@pytest.mark.parametrize(
    ("heading", "markdown", "text"),
    [
        pytest.param(
            "A picture",
            "-   Ferrow chart\n\n    <!-- image -->\n    - Gandry\n"
            "- **Hollin**\n    - Iverby",
            "-   Ferrow chart\n    - Gandry\n- Hollin\n    - Iverby",
            id="picture",
        ),
        pytest.param(
            "A linked picture with no alt",
            "-   <!-- image -->\n    - Jorth",
            "-   - Jorth",
            id="no-alt",
        ),
        pytest.param(
            "A table",
            "-   | Kestle | Lorn |\n    | - | - |\n    | 1 | 2 |\n    - Mardle",
            None,
            id="table",
        ),
        pytest.param("A list", "-   - Nethery\n    - Orrin", None, id="list"),
        pytest.param(
            "A figure",
            "-   Pentle sketch\n\n    <!-- image -->\n    - Quarrow",
            "-   Pentle sketch\n    - Quarrow",
            id="figure",
        ),
        pytest.param(
            "A second term",
            "- **Rendle**\n-   Sallow plan\n\n    <!-- image -->\n    - Tarrant",
            "- Rendle\n-   Sallow plan\n    - Tarrant",
            id="second-term",
        ),
        pytest.param(
            "No description",
            "-   Ullock\n\n    <!-- image -->",
            "-   Ullock",
            id="no-description",
        ),
        pytest.param(
            "A description holding a description list",
            "-   Varley\n\n    <!-- image -->\n    - **Wexcombe**\n        - Yalding",
            "-   Varley\n    - Wexcombe\n        - Yalding",
            id="description-list-in-description",
        ),
        pytest.param(
            "In a description",
            "- **Zennick**\n    -   Abberly\n\n        <!-- image -->\n        - Bransby",
            "- Zennick\n    -   Abberly\n        - Bransby",
            id="in-a-description",
        ),
        pytest.param(
            "In a list item",
            "- Coldrey\n    -   Dunster\n\n        <!-- image -->\n        - Eastry",
            "- Coldrey\n    -   Dunster\n        - Eastry",
            id="in-a-list-item",
        ),
        pytest.param(
            "With a checkbox",
            "Quorn\n\n<!-- image -->\n- Rastrick",
            "Quorn\n- Rastrick",
            id="checkbox",
        ),
    ],
)
def test_each_text_surface_writes_what_the_term_holds_inside_it(
    result: Any, heading: str, markdown: str, text: str | None
) -> None:
    """A term with no text of its own is written as P52 writes such a list
    item: what it holds on the term's line, its other lines and the term's
    descriptions indented to the term's content. content.txt and the chunks
    write a picture as its caption alone, so a picture with no alt leaves the
    term's line to its first description. A term docling makes no item of
    (here, one holding a checkbox, which docling reads only in a list item)
    has its picture put in the list in its place rather than nowhere."""
    text = markdown if text is None else text
    assert _section(result.markdown, heading) == markdown
    assert f"\n\n{text}\n\n" in result.text
    # A chunk is content.txt's text with a term's bold kept, as content.md's.
    chunk = text
    for term in ("Hollin", "Rendle", "Wexcombe", "Zennick"):
        chunk = chunk.replace(f"- {term}\n", f"- **{term}**\n")
    assert chunk in [seed.text for seed in result.chunk_seeds]


def test_commonmark_reads_what_each_term_holds_as_the_term_s(result: Any) -> None:
    """Read back, the picture or table is inside the term's item, and its
    descriptions are a list inside that item after it, as a description is
    written under a term with text. No line becomes a code block."""
    reader = _reader()
    assert reader.render(_section(result.markdown, "A picture")) == (
        "<ul>\n<li>\n<p>Ferrow chart</p>\n<!-- image -->\n<ul>\n<li>Gandry</li>\n"
        "</ul>\n</li>\n<li>\n<p><strong>Hollin</strong></p>\n<ul>\n<li>Iverby</li>\n"
        "</ul>\n</li>\n</ul>\n"
    )
    assert reader.render(_section(result.markdown, "A table")) == (
        "<ul>\n<li>\n<table>\n<thead>\n<tr>\n<th>Kestle</th>\n<th>Lorn</th>\n</tr>\n"
        "</thead>\n<tbody>\n<tr>\n<td>1</td>\n<td>2</td>\n</tr>\n</tbody>\n"
        "</table>\n<ul>\n<li>Mardle</li>\n</ul>\n</li>\n</ul>\n"
    )
    for heading in (
        "A linked picture with no alt",
        "A list",
        "A figure",
        "A second term",
        "No description",
        "A description holding a description list",
        "In a description",
        "In a list item",
    ):
        assert "<pre>" not in reader.render(_section(result.markdown, heading)), heading
    # A term with text: what it holds is inside its item too, after its text,
    # as P61 writes a list item's (P60-5).
    assert reader.render(_section(result.markdown, "With text")) == (
        "<ul>\n<li>\n<p><strong>Hesket</strong></p>\n<p>Ilsley</p>\n<!-- image -->\n"
        "<ul>\n<li>Jessop</li>\n</ul>\n</li>\n</ul>\n"
    )
    assert reader.render(_section(result.markdown, "With text and a table")) == (
        "<ul>\n<li>\n<p><strong>Kirtle</strong></p>\n<table>\n<thead>\n<tr>\n"
        "<th>Lampton</th>\n</tr>\n</thead>\n</table>\n<ul>\n<li>Morley</li>\n</ul>\n"
        "</li>\n</ul>\n"
    )
    assert reader.render(_section(result.markdown, "With a caption")) == (
        "<ul>\n<li>\n<p><strong>Otterly</strong></p>\n<p>Otterly</p>\n<!-- image -->\n"
        "<ul>\n<li>Penrith</li>\n</ul>\n</li>\n</ul>\n"
    )


def test_content_json_holds_the_term_and_what_it_holds(result: Any) -> None:
    """The record holds the `<dt>` the page has: a term with no text, bold as
    docling makes every term, whose children are what it holds and then its
    descriptions. Before, eleven of these terms and everything in them were
    not in it at all."""
    document = json.loads(result.document_json)
    by_ref = {item["self_ref"]: item for item in document["texts"]}
    for kind in ("groups", "pictures", "tables"):
        by_ref.update({item["self_ref"]: item for item in document[kind]})
    empty = [
        item
        for item in document["texts"]
        if item["label"] == "list_item" and not item["text"]
    ]
    held = [
        [
            by_ref[child["$ref"]].get("name") or by_ref[child["$ref"]]["label"]
            for child in term["children"]
        ]
        for term in empty
    ]
    assert held == [
        ["picture", "descriptions"],  # the nav's
        ["picture", "descriptions"],
        ["picture", "descriptions"],  # no alt
        ["table", "descriptions"],
        ["list", "descriptions"],
        ["picture", "descriptions"],  # the figure's
        ["picture", "descriptions"],  # the second term
        ["picture"],  # no description
        ["picture", "description list"],
        ["picture", "descriptions"],  # in a description
        ["picture", "descriptions"],  # in a list item
        ["picture", "descriptions"],  # in the table cell
    ]
    assert all(item["orig"] == "" for item in empty)
    assert all(item["formatting"]["bold"] for item in empty)
    assert len(document["pictures"]) == PICTURES
    assert len(document["tables"]) == 3
    docling_bridge.validate_document_json(result.document_json)


def test_a_term_with_text_keeps_what_it_holds_too(result: Any) -> None:
    """docling made the term of these and dropped what it holds; it is now the
    term's, in the record and in every text surface. A figure's caption is
    read as the term's text by docling, as in a list item, so it is written
    twice, as the term and as the picture's caption."""
    document = json.loads(result.document_json)
    by_ref = {item["self_ref"]: item for item in document["texts"]}
    for kind in ("groups", "pictures", "tables"):
        by_ref.update({item["self_ref"]: item for item in document[kind]})
    terms = {
        item["text"]: [by_ref[child["$ref"]] for child in item["children"]]
        for item in document["texts"]
        if item["text"] in {"Hesket", "Kirtle", "Otterly"}
        and item["label"] == "list_item"
    }
    assert {text: [c["label"] for c in held] for text, held in terms.items()} == {
        "Hesket": ["picture", "list"],
        "Kirtle": ["table", "list"],
        "Otterly": ["picture", "list"],
    }
    for surface in (result.markdown, result.text):
        lines = [line.strip() for line in surface.split("\n")]
        assert {"Ilsley", "| Lampton |", "Otterly"} <= set(lines)
    assert result.tables[2] == [["Lampton"]]


def test_every_picture_keeps_its_ocr_text_inside_its_term(recognized: Any) -> None:
    """With each picture made, the backend's pictures and the snapshot's images
    agree, so every asset's recognized text is attached. Before, docling made
    one picture for fourteen images, and the bridge omitted all fourteen
    annotations with a warning. A term's OCR text is the term's. The nav's
    picture is chrome, so its asset is listed as one (docs/09 P57)."""
    assert recognized.warnings == []
    assert recognized.pictured_asset_ids == [
        f"asset-{n:03d}" for n in range(2, PICTURES + 1)
    ]
    assert recognized.chrome_pictured_asset_ids == ["asset-001"]
    assert _section(recognized.markdown, "A picture").startswith(
        "-   Ferrow chart\n\n"
        "    [OCR text from asset-002, machine-recognized by Tesseract, "
        "mean confidence 90%:]\n    GLYPH2\n    [End of OCR text from asset-002]\n\n"
        "    <!-- image -->\n    - Gandry\n"
    )
    figures = [seed for seed in recognized.chunk_seeds if seed.kind == "figure"]
    assert len(figures) == PICTURES


def test_a_nav_term_stays_out_of_the_body(result: Any) -> None:
    """An empty term goes where what it holds goes: the nav's picture is
    chrome, so its term is too, and content.md does not open with it."""
    document = json.loads(result.document_json)
    nav_term = document["texts"][0]
    assert (nav_term["label"], nav_term["text"]) == ("list_item", "")
    assert nav_term["content_layer"] == "furniture"
    assert document["pictures"][0]["content_layer"] == "furniture"
    assert "Harbour" not in result.markdown
    assert result.markdown.startswith("# Terms that hold")


def test_a_term_before_the_first_heading_reads_as_after_one() -> None:
    """Outside chrome, a description list above the first heading is moved to
    the body a group at a time (P53) and its picture with it (P57), so its
    empty term holds the picture and its description as after a heading
    (P60-5)."""
    dl = (
        "<dl><dt><img src='a.png' alt='Ferrow chart' data-webshot-asset-id="
        "'asset-001'></dt><dd>Gandry</dd></dl>"
    )
    body = (
        '<!doctype html><html><head><meta charset="utf-8"></head><body>{}</body></html>'
    )
    above = extract(body.format(f"{dl}<h1>T</h1><p>Body.</p>"), []).markdown
    below = extract(body.format(f"<h1>T</h1>{dl}"), []).markdown
    held = "-   Ferrow chart\n\n    <!-- image -->\n    - Gandry\n"
    assert above == f"{held}\n# T\n\nBody.\n"
    assert below == f"# T\n\n{held}"


def test_a_table_cell_writes_the_picture_after_the_term_s_marker(result: Any) -> None:
    """docling writes a cell's list on one line, as for a list item (P52), and
    the cell's own text has no space where the stand-in was."""
    assert _section(result.markdown, "In a table").endswith(
        "| - Fennick  <!-- image -->     - Garrow |"
    )
    assert result.tables[1] == [["Terms"], ["Garrow"]]


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
            assert "\N{ZERO WIDTH NO-BREAK SPACE}" not in surface
    assert result.warnings == []


def test_only_the_terms_lines_change(result: Any, monkeypatch: Any) -> None:
    """Against docling's own reading, content.md gains what each term holds
    and the lines of the terms that hold nothing else; each of those terms'
    descriptions moves under it, and the table cell holds its picture. Every
    other line, the term with nothing of its own and a description holding a
    picture included, is docling's. A term with text writes what it holds at
    its content column after a blank line, as P61 writes a list item's."""
    _unfixed(monkeypatch)
    unfixed = extract(FIXTURE.read_text("utf-8"), []).markdown.split("\n")
    fixed = result.markdown.split("\n")
    replaced: list[tuple[list[str], list[str]]] = []
    inserted: list[list[str]] = []
    matcher = difflib.SequenceMatcher(a=unfixed, b=fixed, autojunk=False)
    for tag, a0, a1, b0, b1 in matcher.get_opcodes():
        if tag == "insert":
            inserted.append(fixed[b0:b1])
        elif tag != "equal":
            replaced.append((unfixed[a0:a1], fixed[b0:b1]))
    picture = ["", "    <!-- image -->"]
    assert replaced == [
        (["- Gandry"], ["-   Ferrow chart", *picture, "    - Gandry"]),
        (["- Jorth"], ["-   <!-- image -->", "    - Jorth"]),
        (
            ["- Mardle"],
            ["-   | Kestle | Lorn |", "    | - | - |", "    | 1 | 2 |", "    - Mardle"],
        ),
        (["- Orrin"], ["-   - Nethery", "    - Orrin"]),
        (["- Quarrow"], ["-   Pentle sketch", *picture, "    - Quarrow"]),
        (["- Tarrant"], ["-   Sallow plan", *picture, "    - Tarrant"]),
        (
            ["    - Bransby"],
            ["    -   Abberly", "", "        <!-- image -->", "        - Bransby"],
        ),
        (
            ["    - Eastry"],
            ["    -   Dunster", "", "        <!-- image -->", "        - Eastry"],
        ),
        (["| Garrow |"], ["| - Fennick  <!-- image -->     - Garrow |"]),
    ]
    assert inserted == [
        ["-   Ullock", *picture, ""],
        ["", "-   Varley", *picture, "    - **Wexcombe**", "        - Yalding"],
        ["", "  Ilsley", "", "  <!-- image -->"],
        ["", "  | Lampton |", "  | - |"],
        ["", "  Otterly", "", "  <!-- image -->"],
        ["Quorn", "", "<!-- image -->"],
    ]


@pytest.mark.parametrize(
    "fixture",
    [
        "item_holding_a_picture.html",
        "item_holding_a_list.html",
        "item_code_blocks.html",
    ],
)
def test_a_page_without_a_term_reads_as_before(fixture: str, monkeypatch: Any) -> None:
    """Reading terms changes nothing for a list item: P45's, P50's and P52's
    pages read the same without it. P50's terms hold text and code, and a
    term's code stays in its line, as P50 leaves it."""
    page = (FIXTURES / fixture).read_text("utf-8")
    read = extract(page, [])
    monkeypatch.setattr(docling_bridge, "_reading_terms", lambda base: base)
    before = extract(page, [])
    assert (read.document_json, read.markdown, read.text, read.doctags) == (
        before.document_json,
        before.markdown,
        before.text,
        before.doctags,
    )
    assert read.chunk_seeds == before.chunk_seeds


@pytest.mark.parametrize(
    "term",
    [
        pytest.param("<dt><img src='a.png' alt='a'></dt>", id="picture"),
        pytest.param("<dt><a href='x'><img src='a.png'></a></dt>", id="linked"),
        pytest.param("<dt><figure><img src='a.png'></figure></dt>", id="figure"),
        pytest.param("<dt><table><tr><td>a</td></tr></table></dt>", id="table"),
        pytest.param("<dt><ul><li>a</li></ul></dt>", id="list"),
        pytest.param("<dt><dl><dt>a</dt></dl></dt>", id="description-list"),
    ],
)
def test_a_term_holding_only_a_picture_a_table_or_a_list_is_marked(term: str) -> None:
    marked = mark_empty_items(f"<dl>{term}<dd>b</dd></dl>")
    assert marked.startswith("\N{ZERO WIDTH NO-BREAK SPACE}")
    assert marked.count(EMPTY_ITEM) == 1
    assert marked.index(EMPTY_ITEM) == len("\N{ZERO WIDTH NO-BREAK SPACE}<dl><dt>")


@pytest.mark.parametrize(
    "page",
    [
        pytest.param("<dl><dt>a <img src='a.png'></dt></dl>", id="text"),
        pytest.param(
            "<dl><dt><figure><img src='a.png'><figcaption>a</figcaption></figure>"
            "</dt></dl>",
            id="caption",
        ),
        pytest.param(
            "<dl><dt><input type='checkbox'><img src='a.png'></dt></dl>", id="box"
        ),
        pytest.param("<dl><dt>a</dt><dd><img src='a.png'></dd></dl>", id="description"),
        pytest.param("<div><dt><img src='a.png'></dt></div>", id="outside-a-list"),
    ],
)
def test_anything_else_is_left_to_docling(page: str) -> None:
    """docling's own reading of these is kept: a term with text beside its
    picture, or a figure's caption, which docling reads as its text; a
    checkbox, which docling reads in no term; a description, whose picture
    docling keeps without an item (P60); and a term outside a description
    list, which docling does not read as one."""
    assert mark_empty_items(page) is page


@pytest.mark.parametrize(
    ("character", "content"),
    [
        ("\ufdd2", "<img src='a.png' alt='Ferrow'>"),
        ("&#xFDD2;", "<table><tr><td>Kestle</td></tr></table>"),
    ],
)
def test_a_page_carrying_the_stand_in_says_what_it_did_not_place(
    character: str, content: str
) -> None:
    """A page with U+FDD2 of its own is not marked, so a term holding only a
    picture or a table has no item. What it holds is still read, into the
    list in the term's place, beside the term's description; the warning
    says so."""
    page = (
        '<html><head><meta charset="utf-8"></head><body><h1>T</h1>'
        f"<p>odd {character} kept</p><dl><dt>{content}</dt><dd>Gandry</dd></dl>"
        "</body></html>"
    )
    result = extract(page, [])
    assert "Ferrow" in result.markdown or "| Kestle |" in result.markdown
    assert "\n- Gandry\n" in result.markdown
    warning = next(w for w in result.warnings if "U+FDD2" in w)
    assert "a description-list term holding only those has no line" in warning


def test_the_backend_still_has_what_reading_terms_uses() -> None:
    """The methods `_reading_terms` overrides or calls are private to docling,
    so an upgrade that renames one is caught here rather than by a page."""
    from docling.backend import html_backend

    missing = [
        hook
        for hook in docling_bridge._TERM_HOOKS
        if not hasattr(html_backend.HTMLDocumentBackend, hook)
    ]
    assert not missing


def test_without_those_methods_a_term_is_read_as_docling_reads_it(
    monkeypatch: Any,
) -> None:
    """With one gone, the page is read without the term reader, so a term's
    picture is dropped again, and the record says so."""
    monkeypatch.setattr(
        docling_bridge,
        "_TERM_HOOKS",
        (*docling_bridge._TERM_HOOKS, "_a_method_docling_renamed"),
    )
    page = (
        "<html><body><h1>T</h1><dl><dt>Hesket <img src='a.png' alt='Ilsley'></dt>"
        "<dd>Jessop</dd></dl></body></html>"
    )
    result = extract(page, [])
    assert result.picture_count == 0
    assert "Ilsley" not in result.markdown
    warning = next(w for w in result.warnings if "docs/09 P60" in w)
    assert "a picture, a table or a list in a term" in warning
