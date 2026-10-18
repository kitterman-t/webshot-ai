"""A description keeps its text beside the description list it holds (docs/09 P73).

docling's HTML backend reads a description (`<dd>`) whose child is a
description list for that list alone: it hangs the list off the term, so a
description holding nothing else is not indented twice, and never reads the
description's own text. Text beside the list was dropped from content.json,
content.md, content.txt and the chunks, with no warning, and with no term to
hang it off, the list was dropped too.

`description_holding_a_list.html` holds each shape, and four that lose
nothing and must read as docling reads them. content.md is read back with
markdown-it-py, a CommonMark reader (here with GFM tables), as in P45's, P52's
and P60's tests.
"""

from __future__ import annotations

import difflib
import json
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest
from markdown_it import MarkdownIt

from webshot.capture.visuals import VisualAsset
from webshot.extract import docling_bridge
from webshot.extract.docling_bridge import EMPTY_ITEM, extract
from webshot.ocr.tesseract import OCRResult

FIXTURES = Path(__file__).resolve().parent / "fixtures"
FIXTURE = FIXTURES / "description_holding_a_list.html"
#: The pictures in the fixture, in document order.
PICTURES = 3
#: The fixture's sections that lose nothing on docling's own reading.
UNCHANGED = (
    "The list alone",
    "A picture and the list",
    "A line break and the list",
    "The list in a div",
)


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
    """docling's own reading of a description list, P50's code and P60's
    terms kept."""
    monkeypatch.setattr(docling_bridge, "_reading_descriptions", lambda base: base)


def _section(markdown: str, heading: str) -> str:
    """The lines under `## heading` in `markdown`, up to the next heading."""
    after = markdown.split(f"\n## {heading}\n\n", 1)[1]
    return after.split("\n## ", 1)[0].strip("\n")


def _surfaces(extracted: Any) -> tuple[Any, ...]:
    return (
        extracted.document_json,
        extracted.markdown,
        extracted.text,
        extracted.doctags,
        extracted.chunk_seeds,
        extracted.tables,
        extracted.picture_count,
        extracted.item_count,
        extracted.warnings,
    )


def _backend(page: str) -> Any:
    """The bridge's backend over `page`, as `extract` builds it."""
    from docling.backend.html_backend import HTMLDocumentBackend
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.document import InputDocument

    data = page.encode("utf-8")
    in_doc = InputDocument(
        path_or_stream=BytesIO(data),
        format=InputFormat.HTML,
        backend=HTMLDocumentBackend,
        filename="content.html",
    )
    backend = docling_bridge._reading_descriptions(
        docling_bridge._reading_terms(docling_bridge._code_block_backend())
    )
    return backend(in_doc=in_doc, path_or_stream=BytesIO(data))


@pytest.fixture(scope="module")
def result() -> Any:
    return extract(FIXTURE.read_text("utf-8"), [])


@pytest.fixture(scope="module")
def recognized() -> Any:
    return extract(
        FIXTURE.read_text("utf-8"), [_asset(n) for n in range(1, PICTURES + 1)]
    )


@pytest.mark.parametrize(
    ("heading", "markdown", "text", "chunk"),
    [
        pytest.param(
            "Text before the list",
            "- **Brisk**\n    - Callow\n        - **Ash**\n            - Birch",
            None,
            None,
            id="before",
        ),
        pytest.param(
            "Text after the list",
            "- **Dorrel**\n    - Gorran\n        - **Eskan**\n            - Fennet",
            None,
            None,
            id="after",
        ),
        pytest.param(
            "Text on both sides",
            "- **Hask**\n    - Ibbet Lommer\n        - **Jorrow**\n            - Kessan",
            None,
            None,
            id="both-sides",
        ),
        pytest.param(
            "Text in a paragraph",
            "- **Marn**\n    - Nettrel\n        - **Orsk**\n            - Pellan",
            None,
            None,
            id="paragraph",
        ),
        pytest.param(
            "Linked and bold text",
            "- **Quiller**\n    - [Rasker](https://example.com/rasker) and "
            "**Sollen**\n        - **Tobbit**\n            - Ummer",
            "- Quiller\n    - Rasker and Sollen\n        - Tobbit\n            - Ummer",
            None,
            id="linked-and-bold",
        ),
        pytest.param(
            "Several descriptions",
            "- **Gimble**\n    - Hollard\n    - Ivett\n        - **Jassen**\n"
            "            - Kimber\n    - Larrow",
            None,
            None,
            id="several-descriptions",
        ),
        pytest.param(
            "A second term",
            "- **Mossit**\n    - Nubble\n        - **Orrel**\n            - Pask\n"
            "- **Quorrin**\n    - Rellet",
            None,
            None,
            id="second-term",
        ),
        pytest.param(
            "A term holding only a picture",
            "-   Sannet plan\n\n    <!-- image -->\n    - Torrow\n"
            "        - **Ullen**\n            - Vask",
            "-   Sannet plan\n    - Torrow\n        - Ullen\n            - Vask",
            "-   Sannet plan\n    - Torrow\n        - **Ullen**\n            - Vask",
            id="term-holding-a-picture",
        ),
        pytest.param(
            "No term",
            "- Wemmet\n    - **Yollow**\n        - Zannet",
            None,
            None,
            id="no-term",
        ),
        pytest.param(
            "An empty term",
            "- Abble\n    - **Bresk**\n        - Cobben",
            None,
            None,
            id="empty-term",
        ),
        pytest.param(
            "Two levels",
            "- **Fasset**\n    - Gellow\n        - **Hobben**\n            - Isset\n"
            "                - **Jemmer**\n                    - Kollow",
            None,
            None,
            id="two-levels",
        ),
        pytest.param(
            "In a list item",
            "- Lassen\n    - **Mibber**\n        - Nollet\n            - **Ossen**\n"
            "                - Pommer",
            None,
            None,
            id="in-a-list-item",
        ),
    ],
)
def test_each_text_surface_writes_the_description_over_its_list(
    result: Any, heading: str, markdown: str, text: str | None, chunk: str | None
) -> None:
    """A description is written as docling writes one with no description
    list of its own: an item with its text under its term, and the list it
    holds nested under that item, as a list item holding text and a list is
    written. Its text is all of its own text, wherever it stands beside the
    list, as for a list item. content.txt writes no bold, and content.txt and
    the chunks write a picture as its caption alone."""
    text = markdown.replace("**", "") if text is None else text
    chunk = markdown if chunk is None else chunk
    assert _section(result.markdown, heading) == markdown
    assert f"\n\n{text}\n\n" in result.text
    assert chunk in [seed.text for seed in result.chunk_seeds]


def test_commonmark_reads_each_list_inside_its_description(result: Any) -> None:
    """Read back, the description list is inside the description's item, after
    its text, and the description inside its term. No line of these sections
    becomes a code block."""
    reader = _reader()
    assert reader.render(_section(result.markdown, "Text before the list")) == (
        "<ul>\n<li><strong>Brisk</strong>\n<ul>\n<li>Callow\n<ul>\n"
        "<li><strong>Ash</strong>\n<ul>\n<li>Birch</li>\n</ul>\n</li>\n</ul>\n"
        "</li>\n</ul>\n</li>\n</ul>\n"
    )
    assert reader.render(_section(result.markdown, "Several descriptions")) == (
        "<ul>\n<li><strong>Gimble</strong>\n<ul>\n<li>Hollard</li>\n<li>Ivett\n"
        "<ul>\n<li><strong>Jassen</strong>\n<ul>\n<li>Kimber</li>\n</ul>\n</li>\n"
        "</ul>\n</li>\n<li>Larrow</li>\n</ul>\n</li>\n</ul>\n"
    )
    assert reader.render(_section(result.markdown, "No term")) == (
        "<ul>\n<li>Wemmet\n<ul>\n<li><strong>Yollow</strong>\n<ul>\n"
        "<li>Zannet</li>\n</ul>\n</li>\n</ul>\n</li>\n</ul>\n"
    )
    for heading in (
        "Text after the list",
        "Text on both sides",
        "Text in a paragraph",
        "Linked and bold text",
        "A second term",
        "A term holding only a picture",
        "An empty term",
        "Two levels",
        "In a list item",
        *UNCHANGED[:1],
        *UNCHANGED[2:],
    ):
        assert "<pre>" not in reader.render(_section(result.markdown, heading)), heading


def test_content_json_holds_each_description_over_its_list(result: Any) -> None:
    """The record holds the `<dd>` the page has: an item with its text, among
    its term's descriptions, holding the description list and whatever else
    it holds. With no term, docling puts a description in the list in the
    term's place, and this one goes there too. Before, none of these items was
    in the record, and the lists with no term were not in it at all."""
    document = json.loads(result.document_json)
    by_ref = {item["self_ref"]: item for item in document["texts"]}
    for array in ("groups", "pictures", "tables"):
        by_ref.update({item["self_ref"]: item for item in document[array]})

    def kind(ref: str) -> str:
        node = by_ref[ref]
        return str(node.get("name") or node["label"])

    found: dict[str, tuple[str | None, list[str]]] = {}
    for item in document["texts"]:
        if item["label"] != "list_item" or item.get("formatting"):
            continue
        children = [kind(child["$ref"]) for child in item["children"]]
        if "description list" not in children:
            continue
        group = by_ref[item["parent"]["$ref"]]
        if group["name"] not in {"descriptions", "description list"}:
            continue  # a list item's, `<li>Lassen<dl>…`
        term = (
            by_ref[group["parent"]["$ref"]]["text"]
            if group["name"] == "descriptions"
            else None
        )
        text = item["text"] or " ".join(
            by_ref[part["$ref"]]["text"]
            for child in item["children"]
            if kind(child["$ref"]) == "group"
            for part in by_ref[child["$ref"]]["children"]
        )
        found[text] = (term, children)
    assert found == {
        "Jettle": ("Harbour", ["description list"]),
        "Callow": ("Brisk", ["description list"]),
        "Gorran": ("Dorrel", ["description list"]),
        "Ibbet Lommer": ("Hask", ["description list"]),
        "Nettrel": ("Marn", ["description list"]),
        "Rasker and Sollen": ("Quiller", ["group", "description list"]),
        "Wendle": ("Vessick", ["picture", "description list"]),
        "Cresset": ("Bellock", ["table", "description list"]),
        "Ivett": ("Gimble", ["description list"]),
        "Nubble": ("Mossit", ["description list"]),
        "Torrow": ("", ["description list"]),  # P60's empty term
        "Wemmet": (None, ["description list"]),
        "Abble": (None, ["description list"]),
        "Gellow": ("Fasset", ["description list"]),
        "Isset": ("Hobben", ["description list"]),
        "Nollet": ("Mibber", ["description list"]),
        "Rabbet": ("Quessel", ["description list"]),
        "Gassen": ("Forrel", ["description list"]),  # in a <div>: docling's own
    }
    texts = [item["text"] for item in document["texts"]]
    # A description among others stays in its term's one group of them.
    hollard = by_ref[document["texts"][texts.index("Hollard")]["parent"]["$ref"]]
    assert [by_ref[child["$ref"]]["text"] for child in hollard["children"]] == [
        "Hollard",
        "Ivett",
        "Larrow",
    ]
    assert len(document["pictures"]) == PICTURES
    docling_bridge.validate_document_json(result.document_json)


def test_a_picture_or_table_beside_the_text_is_the_description_s(
    result: Any,
) -> None:
    """docling hung them off the term; they are now inside the description,
    before its list, as in a list item holding text, a picture and a list.
    content.md writes them as P61 writes such an item's: at the description's
    content column after a blank line, where a CommonMark reader reads them,
    and the list after them, as the description's (docs/09 P61)."""
    picture = _section(result.markdown, "A picture beside the text")
    assert picture == (
        "- **Vessick**\n    - Wendle\n\n      Yarrin chart\n\n      <!-- image -->\n"
        "        - **Zobel**\n            - Abbany"
    )
    assert _reader().render(picture) == (
        "<ul>\n<li><strong>Vessick</strong>\n<ul>\n<li>\n<p>Wendle</p>\n"
        "<p>Yarrin chart</p>\n<!-- image -->\n<ul>\n<li><strong>Zobel</strong>\n"
        "<ul>\n<li>Abbany</li>\n</ul>\n</li>\n</ul>\n</li>\n</ul>\n</li>\n</ul>\n"
    )
    table = _section(result.markdown, "A table beside the text")
    assert table == (
        "- **Bellock**\n    - Cresset\n\n      | Dannow |\n      | - |\n"
        "        - **Ernley**\n            - Fosk"
    )
    assert _reader().render(table) == (
        "<ul>\n<li><strong>Bellock</strong>\n<ul>\n<li>\n<p>Cresset</p>\n<table>\n"
        "<thead>\n<tr>\n<th>Dannow</th>\n</tr>\n</thead>\n</table>\n<ul>\n"
        "<li><strong>Ernley</strong>\n<ul>\n<li>Fosk</li>\n</ul>\n</li>\n</ul>\n"
        "</li>\n</ul>\n</li>\n</ul>\n"
    )
    assert result.tables[0] == [["Dannow"]]


def test_a_description_with_no_term_and_no_text_is_placed_in_the_list(
    result: Any,
) -> None:
    """docling dropped it, and its list with it. It is now read as docling
    reads any other description with no term and no text of its own: what it
    holds goes in the list in its place. content.md writes that list as
    docling writes a `<ul>` in such a description, four columns in at the
    margin, which a CommonMark reader takes for a code block (docs/09 P73-5);
    the text is in every surface."""
    markdown = "    - **Dorrow**\n        - Effen"
    assert _section(result.markdown, "No term and no text") == markdown
    assert "<pre>" in _reader().render(markdown)
    assert "\n\n    - Dorrow\n        - Effen\n\n" in result.text
    assert markdown in [seed.text for seed in result.chunk_seeds]


def test_a_table_cell_writes_the_description_on_its_line(result: Any) -> None:
    """docling writes a cell's list on one line. The cell's own text, which
    docling reads from the page, held the description's text before too."""
    assert _section(result.markdown, "In a table").endswith(
        "| - **Quessel**     - Rabbet         - **Sennow**             - Tassel |"
    )
    assert result.tables[1] == [["Terms"], ["QuesselRabbetSennowTassel"]]


def test_every_picture_keeps_its_ocr_text(recognized: Any) -> None:
    """The pictures are docling's, made in document order as before, so they
    pair with the snapshot's images one for one and every asset's recognized
    text is attached; the description's picture carries it into the
    description."""
    assert recognized.warnings == []
    assert recognized.pictured_asset_ids == [
        f"asset-{n:03d}" for n in range(1, PICTURES + 1)
    ]
    assert _section(recognized.markdown, "A picture beside the text").startswith(
        "- **Vessick**\n    - Wendle\n\n      Yarrin chart\n\n"
        "      [OCR text from asset-001, machine-recognized by Tesseract, "
        "mean confidence 90%:]\n      GLYPH1\n      [End of OCR text from asset-001]\n"
    )
    figures = [seed for seed in recognized.chunk_seeds if seed.kind == "figure"]
    assert len(figures) == PICTURES


def test_a_nav_description_stays_out_of_the_body(result: Any) -> None:
    """The nav's description, now read, is chrome as the rest of its list is,
    and content.md does not open with it."""
    document = json.loads(result.document_json)
    jettle = next(item for item in document["texts"] if item["text"] == "Jettle")
    assert jettle["content_layer"] == "furniture"
    assert "Jettle" not in result.markdown
    assert result.markdown.startswith("# Descriptions that hold")


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
            assert "" not in surface
    assert result.warnings == []


def test_only_the_descriptions_lines_change(result: Any, monkeypatch: Any) -> None:
    """Against docling's own reading of a description list, content.md gains
    each description's line, its list moves under it, and the lists with no
    term appear. Every other line, the four shapes that lose nothing among
    them, is docling's. The nav's list leaves content.md: docling's reading
    drops its description's text, so the list no longer spells the nav's
    text and the bridge's chrome test moves it to the body; read whole, it
    is the nav's and stays chrome."""
    _unfixed(monkeypatch)
    unfixed = extract(FIXTURE.read_text("utf-8"), []).markdown.split("\n")
    fixed = result.markdown.split("\n")
    changes: list[tuple[str, list[str], list[str]]] = []
    matcher = difflib.SequenceMatcher(a=unfixed, b=fixed, autojunk=False)
    for tag, a0, a1, b0, b1 in matcher.get_opcodes():
        if tag != "equal":
            changes.append((tag, unfixed[a0:a1], fixed[b0:b1]))

    def under(
        description: str, term: str, held: str, indent: str = ""
    ) -> tuple[str, list[str], list[str]]:
        """The term's list hung off the term, now under its description."""
        return (
            "replace",
            [f"{indent}    - **{term}**", f"{indent}        - {held}"],
            [
                f"{indent}    - {description}",
                f"{indent}        - **{term}**",
                f"{indent}            - {held}",
            ],
        )

    assert changes == [
        ("delete", ["- **Harbour**", "    - **Kessock**", "        - Lossel", ""], []),
        under("Callow", "Ash", "Birch"),
        under("Gorran", "Eskan", "Fennet"),
        under("Ibbet Lommer", "Jorrow", "Kessan"),
        under("Nettrel", "Orsk", "Pellan"),
        under("[Rasker](https://example.com/rasker) and **Sollen**", "Tobbit", "Ummer"),
        # P61 writes the picture and the table at the content column of the
        # item that holds them: the term's in docling's reading, the
        # description's now.
        ("insert", [], ["    - Wendle"]),
        ("replace", ["  Yarrin chart"], ["      Yarrin chart"]),
        (
            "replace",
            ["  <!-- image -->", "    - **Zobel**", "        - Abbany"],
            ["      <!-- image -->", "        - **Zobel**", "            - Abbany"],
        ),
        ("insert", [], ["    - Cresset"]),
        (
            "replace",
            ["  | Dannow |", "  | - |", "    - **Ernley**", "        - Fosk"],
            [
                "      | Dannow |",
                "      | - |",
                "        - **Ernley**",
                "            - Fosk",
            ],
        ),
        under("Ivett", "Jassen", "Kimber"),
        under("Nubble", "Orrel", "Pask"),
        under("Torrow", "Ullen", "Vask"),
        ("insert", [], ["- Wemmet", "    - **Yollow**", "        - Zannet", ""]),
        ("insert", [], ["- Abble", "    - **Bresk**", "        - Cobben", ""]),
        ("insert", [], ["", "    - **Dorrow**", "        - Effen"]),
        (
            "replace",
            ["    - **Hobben**", "        - **Jemmer**", "            - Kollow"],
            [
                "    - Gellow",
                "        - **Hobben**",
                "            - Isset",
                "                - **Jemmer**",
                "                    - Kollow",
            ],
        ),
        under("Nollet", "Ossen", "Pommer", indent="    "),
        (
            "replace",
            ["| - **Quessel**     - **Sennow**         - Tassel |"],
            [
                "| - **Quessel**     - Rabbet         - **Sennow**             - Tassel |"
            ],
        ),
    ]


def test_what_loses_nothing_reads_as_docling_reads_it(monkeypatch: Any) -> None:
    """A description with a term and no text of its own beside its list, a
    picture or a line break, is left to docling, which hangs the list off the
    term, so as not to indent it twice; so is a list in a `<div>`, which
    docling already reads as the description's. These shapes read the same on
    every surface with docling's own reading."""
    page = FIXTURE.read_text("utf-8")
    head, _, body = page.partition("<h2>")
    sections = [f"<h2>{part}" for part in body.split("<h2>")]
    kept = [part for part in sections if part.split("</h2>")[0][4:] in UNCHANGED]
    assert len(kept) == len(UNCHANGED)
    only = head.split("<nav>")[0] + "<h1>T</h1>\n" + "".join(kept)
    if not only.rstrip().endswith("</html>"):
        only += "</body></html>\n"
    read = extract(only, [_asset(3)])
    _unfixed(monkeypatch)
    assert _surfaces(read) == _surfaces(extract(only, [_asset(3)]))
    assert read.pictured_asset_ids == ["asset-003"]


@pytest.mark.parametrize(
    "fixture",
    [
        "term_holding_a_picture.html",
        "item_holding_a_picture.html",
        "item_holding_a_list.html",
        "item_code_blocks.html",
        "structures_page.html",
    ],
)
def test_other_pages_read_as_before(fixture: str, monkeypatch: Any) -> None:
    """P45's, P50's, P52's and P60's pages and the golden corpus's description
    lists hold no description with text beside a description list, and read
    the same on every surface with docling's own reading of one. P60's has
    descriptions holding only a description list, under a term, and P50's
    descriptions holding code."""
    page = (FIXTURES / fixture).read_text("utf-8")
    read = extract(page, [])
    _unfixed(monkeypatch)
    assert _surfaces(read) == _surfaces(extract(page, []))


@pytest.mark.parametrize(
    ("content", "text"),
    [
        pytest.param("Callow", True, id="text"),
        pytest.param("<p>Callow</p>", True, id="paragraph"),
        pytest.param("<a href='https://example.com/c'>Callow</a>", True, id="link"),
        pytest.param("<pre>x = 1</pre>", False, id="code"),
        pytest.param(
            "<figure><img src='a.png'><figcaption>Callow</figcaption></figure>",
            True,
            id="caption",
        ),
        pytest.param("", False, id="empty"),
        pytest.param(" \n\t ", False, id="whitespace"),
        pytest.param("&nbsp;", False, id="no-break-space"),
        pytest.param("<!-- Callow -->", False, id="comment"),
        pytest.param("<span hidden>Callow</span>", False, id="hidden"),
        pytest.param("<img src='a.png' alt='Callow'>", False, id="picture"),
        pytest.param("<table><tr><td>Callow</td></tr></table>", False, id="table"),
        pytest.param("<ul><li>Callow</li></ul>", False, id="list"),
        pytest.param("<input type='checkbox'>", False, id="checkbox"),
    ],
)
def test_text_of_its_own_is_the_text_of_docling_s_item(
    content: str, text: bool
) -> None:
    """A description is read as one exactly when docling would give its item
    text, which `_add_list_item_with_content` takes from the same reading: here,
    for a description docling already reads as one. A description holding
    only code is an item with no text, the code under it, as P50 reads it."""
    page = (
        f"<html><body><h1>T</h1><dl><dt>Brisk</dt><dd>{content}</dd></dl></body></html>"
    )
    backend = _backend(page)
    own = backend._own_text(backend.soup.find("dd"))
    document = backend.convert().export_to_dict()
    by_ref = {item["self_ref"]: item for item in document["texts"]}
    by_ref.update({item["self_ref"]: item for item in document["groups"]})
    items = [
        by_ref[child["$ref"]]
        for group in document["groups"]
        if group["name"] == "descriptions"
        for child in group["children"]
        # The items docling made; with none, what the description holds.
        if child["$ref"].startswith("#/texts/")
    ]

    def written(item: dict[str, Any]) -> str:
        """The item's text, or its inline group's, as content.md writes it."""
        return item["text"] or " ".join(
            by_ref[part["$ref"]]["text"]
            for child in item["children"]
            if by_ref[child["$ref"]].get("name") == "group"
            for part in by_ref[child["$ref"]]["children"]
        )

    assert (bool(own), any(written(item) for item in items)) == (text, text)


@pytest.mark.parametrize(
    "page",
    [
        pytest.param(
            "<dl><dt>Brisk</dt><dd><br><dl><dt>Ash</dt><dd>Birch</dd></dl></dd></dl>",
            id="description-a-line-break",
        ),
        pytest.param(
            "<dl><dt>Brisk</dt><dd><pre>x = 1</pre><dl><dt>Ash</dt><dd>Birch</dd>"
            "</dl></dd></dl>",
            id="description-code",
        ),
    ],
)
def test_a_line_break_or_code_is_no_description_s_text(
    page: str, monkeypatch: Any
) -> None:
    """Beside a description list a `<br>` is no text of the description's: it
    loses only the line break as docling reads it, and made an item it would
    carry U+E000 to every surface (docs/09 P56). A description's code is no
    text of it either, but a block P50 reads under the term with the list.
    Both read as docling reads them."""
    page = f"<html><body><h1>T</h1>{page}</body></html>"
    read = extract(page, [])
    assert "\ue000" not in read.document_json
    _unfixed(monkeypatch)
    assert _surfaces(read) == _surfaces(extract(page, []))


def test_a_term_that_is_only_a_line_break_is_no_term(monkeypatch: Any) -> None:
    """P56 takes the `<br>` out of a term with no other text before docling
    reads the page, so docling makes no term of it, as of an empty `<dt>`.
    The description is then one with no term, read as P73 reads that: its
    list is placed in the description list, where docling's reading dropped
    it with the description. No U+E000 reaches the record."""

    def read(term: str) -> Any:
        return extract(
            f"<html><body><h1>T</h1><dl>{term}<dd><dl><dt>Ash</dt><dd>Birch</dd>"
            "</dl></dd></dl></body></html>",
            [],
        )

    broken, empty = read("<dt><br></dt>"), read("<dt></dt>")
    assert "\ue000" not in broken.document_json
    # All but content.json, whose origin is the hash of the page as given.
    assert _surfaces(broken)[1:] == _surfaces(empty)[1:]
    assert broken.markdown == "# T\n\n    - **Ash**\n        - Birch\n"
    _unfixed(monkeypatch)
    assert read("<dt><br></dt>").markdown == "# T\n"


def test_the_page_is_left_as_parsed() -> None:
    """The wrapper a description's list is read through is taken out once its
    list is built, so the tree holds only what the page had: every
    description list has the parent it was parsed with."""
    backend = _backend(FIXTURE.read_text("utf-8"))
    parents = [inner.parent.name for inner in backend.soup.find_all("dl")]
    assert parents.count("dd") == 21
    backend.convert()
    assert [inner.parent.name for inner in backend.soup.find_all("dl")] == parents
    assert len(backend.soup.find_all("div")) == 1


def test_the_backend_still_has_what_reading_descriptions_uses() -> None:
    """The methods `_reading_descriptions` overrides or calls are private to
    docling, so an upgrade that renames one is caught here rather than by a
    page."""
    from docling.backend import html_backend

    missing = [
        hook
        for hook in docling_bridge._DESCRIPTION_HOOKS
        if not hasattr(html_backend.HTMLDocumentBackend, hook)
    ]
    assert not missing
    assert docling_bridge._descriptions_hooked()


def test_without_those_methods_a_description_is_read_as_docling_reads_it(
    monkeypatch: Any,
) -> None:
    """With one gone, the page is read without the description reader, so a
    description's text beside its list is dropped again, and the record says
    so."""
    monkeypatch.setattr(
        docling_bridge,
        "_DESCRIPTION_HOOKS",
        (*docling_bridge._DESCRIPTION_HOOKS, "_a_method_docling_renamed"),
    )
    page = (
        "<html><body><h1>T</h1><dl><dt>Brisk</dt><dd>Callow<dl><dt>Ash</dt>"
        "<dd>Birch</dd></dl></dd></dl></body></html>"
    )
    result = extract(page, [])
    assert "Callow" not in result.markdown
    assert "- **Ash**" in result.markdown
    warning = next(w for w in result.warnings if "docs/09 P73" in w)
    assert "the description's text beside that list" in warning
