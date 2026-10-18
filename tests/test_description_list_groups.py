"""A description list grouped in `<div>`s keeps its terms and descriptions (docs/09 P72).

HTML lets a `<div>` group a term with its descriptions
(`<dl><div><dt>…</dt><dd>…</dd></div></dl>`), the shape design systems'
summary lists use. docling's HTML backend reads only a description list's own
`<dt>` and `<dd>` children, so a group was dropped from content.json,
content.md, content.txt and the chunks, text and all, with no warning, and a
picture in it left the bridge short of the snapshot's images, so every OCR
annotation on the page was omitted.

`description_list_groups.html` holds each shape. A grouped list is meant to
read exactly as the same list without its groups, so most tests compare the
two rather than pin what docling writes for a list (P45, P52 and P60 pin
that). content.md is read back with markdown-it-py, a CommonMark reader, as in
P60's tests.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
from markdown_it import MarkdownIt

from webshot.capture.visuals import VisualAsset
from webshot.extract import docling_bridge
from webshot.extract.docling_bridge import (
    EMPTY_ITEM,
    ExtractionResult,
    extract,
    mark_empty_items,
)
from webshot.ocr.tesseract import OCRResult

FIXTURES = Path(__file__).resolve().parent / "fixtures"
FIXTURE = FIXTURES / "description_list_groups.html"
#: The pictures in the fixture, in document order.
PICTURES = 3
#: The fixture's one hidden group, with what it holds. Every other `<div>` in
#: the fixture groups terms with their descriptions.
HIDDEN_GROUP = '<div aria-hidden="true"><dt>Nettle</dt><dd>Oxlip</dd></div>'


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


def _ungrouped(html: str) -> str:
    """`html` with its groups' `<div>` tags taken out and what they held left.

    Only for pages whose every `<div>` groups a description list's terms.
    """
    return re.sub(r"<div(?:\s[^>]*)?>|</div>", "", html)


def _surfaces(result: ExtractionResult) -> dict[str, Any]:
    """Everything `extract` hands the bundle writer, but the page's own hash."""
    document = json.loads(result.document_json)
    # The origin records the page as given, which the two pages are not.
    del document["origin"]
    return {
        "content.json": document,
        "content.md": result.markdown,
        "content.txt": result.text,
        "doctags": result.doctags,
        "tables": result.tables,
        "chunks": result.chunk_seeds,
        "items": result.item_count,
        "pictures": result.picture_count,
        "pictured": result.pictured_asset_ids,
        "warnings": result.warnings,
    }


def _page(body: str) -> str:
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8"></head>'
        f"<body><h1>Head</h1>{body}<p>After</p></body></html>"
    )


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


def test_the_reported_list_keeps_its_term_and_description() -> None:
    """The report's page: content.md was `# Head`, then `After`."""
    page = "<h1>Head</h1><dl><div><dt>Brisk</dt><dd>Callow</dd></div></dl><p>After</p>"
    assert (
        extract(page, []).markdown == "# Head\n\n- **Brisk**\n    - Callow\n\nAfter\n"
    )


def test_the_page_reads_as_its_lists_without_their_groups(recognized: Any) -> None:
    """Every surface of the fixture, its OCR text attached, is the same as
    the page's with each group's `<div>` taken out and a hidden group left
    out with what it holds. Before, the fixture's text surfaces held four of
    its terms and descriptions, docling made no picture for its three
    images, and every OCR annotation was omitted."""
    page = FIXTURE.read_text("utf-8")
    assert HIDDEN_GROUP in page
    twin = _ungrouped(page.replace(HIDDEN_GROUP, ""))
    assert "<div" not in twin
    plain = extract(twin, [_asset(n) for n in range(1, PICTURES + 1)])
    assert _surfaces(recognized) == _surfaces(plain)


@pytest.mark.parametrize(
    "body",
    [
        pytest.param("<dl><div><dt>Brisk</dt><dd>Callow</dd></div></dl>", id="pair"),
        pytest.param(
            "<dl><div><dt>Brisk</dt><dd>Callow</dd></div>"
            "<div><dt>Dapple</dt><dd>Eskew</dd></div></dl>",
            id="two-groups",
        ),
        pytest.param(
            "<dl><dt>Brisk</dt><dd>Callow</dd><div><dt>Dapple</dt><dd>Eskew</dd>"
            "</div><dt>Fallow</dt><dd>Gorse</dd></dl>",
            id="mixed",
        ),
        pytest.param(
            "<dl><div><dt>Brisk</dt><dt>Bristle</dt><dd>Callow</dd><dd>Cantle</dd>"
            "</div></dl>",
            id="several",
        ),
        pytest.param(
            "<dl><div><dt>Brisk</dt><dd><dl><div><dt>Dapple</dt><dd>Eskew</dd>"
            "</div></dl></dd></div></dl>",
            id="nested",
        ),
        pytest.param(
            "<dl><div><dt>Brisk</dt><dd>Callow<dl><div><dt>Dapple</dt>"
            "<dd>Eskew</dd></div></dl></dd></div></dl>",
            id="nested-beside-text",
        ),
        pytest.param(
            "<dl><div><dt><img src='a.png' alt='Ferrow' data-webshot-asset-id="
            "'asset-001'></dt><dd>Callow</dd></div></dl>",
            id="picture-term",
        ),
        pytest.param(
            "<dl><div><dt>Brisk <img src='a.png' alt='Ferrow' data-webshot-asset-id="
            "'asset-001'></dt><dd>Callow</dd></div></dl>",
            id="term-with-text-and-picture",
        ),
        pytest.param(
            "<dl><div><dt>Brisk</dt><dd><img src='a.png' alt='Ferrow' "
            "data-webshot-asset-id='asset-001'></dd></div></dl>",
            id="picture-description",
        ),
        pytest.param(
            "<dl><div><dt><ul><li>Nethery</li></ul></dt><dd>Orrin</dd></div></dl>",
            id="list-term",
        ),
        # A line break alone is no text, grouped or not (P56): the term is
        # none, or holds what it holds, and no empty bold term is written.
        pytest.param("<dl><div><dt><br></dt><dd>Callow</dd></div></dl>", id="br-term"),
        pytest.param(
            "<dl><div><dt><br><img src='a.png' alt='Ferrow' data-webshot-asset-id="
            "'asset-001'></dt><dd>Callow</dd></div></dl>",
            id="br-and-picture-term",
        ),
        pytest.param(
            "<dl><div><dt><br><ul><li>Nethery</li></ul></dt><dd>Orrin</dd></div></dl>",
            id="br-and-list-term",
        ),
        pytest.param(
            "<dl><div><dt>Brisk</dt><dd><br></dd></div><div><dt>Dapple</dt>"
            "<dd>Eskew</dd></div></dl>",
            id="br-description",
        ),
        pytest.param(
            "<dl><div><div><dt><br></dt><dd>Callow</dd></div></div></dl>",
            id="br-term-in-a-group-in-a-group",
        ),
        pytest.param(
            "<dl><div><div><dt>Brisk</dt><dd>Callow</dd></div></div></dl>",
            id="group-in-a-group",
        ),
        pytest.param(
            "<dl><div><dt>Brisk</dt></div><div><dd>Callow</dd></div></dl>",
            id="term-and-description-apart",
        ),
        pytest.param(
            "<dl><div><p>Lede</p><dt>Brisk</dt><dd>Callow</dd></div></dl>",
            id="paragraph-in-a-group",
        ),
        pytest.param(
            "<ul><li>Hollin<dl><div><dt>Brisk</dt><dd>Callow</dd></div></dl></li></ul>",
            id="in-a-list-item",
        ),
        pytest.param(
            "<table><tr><th>T</th></tr><tr><td><dl><div><dt>Brisk</dt>"
            "<dd>Callow</dd></div></dl></td></tr></table>",
            id="in-a-table-cell",
        ),
        pytest.param(
            "<nav><dl><div><dt>Brisk</dt><dd><a href='/c'>Callow</a></dd></div>"
            "</dl></nav>",
            id="in-a-nav",
        ),
    ],
)
def test_each_shape_reads_as_its_list_without_its_groups(body: str) -> None:
    """Each shape, on every surface, against the same list ungrouped.

    Where docling's reading of the ungrouped list loses something of its own
    (a description's text beside its description list, a paragraph loose in
    the list), the grouped list loses the same, and no more."""
    assets = [_asset(1)] if "<img" in body else []
    grouped = extract(_page(body), assets)
    plain = extract(_page(_ungrouped(body)), assets)
    assert _surfaces(grouped) == _surfaces(plain)
    # Two pages that both lost the list would compare equal too.
    assert grouped.markdown != extract(_page(""), []).markdown


@pytest.mark.parametrize(
    ("heading", "markdown"),
    [
        pytest.param(
            "Grouped pairs",
            "- **Brisk**\n    - Callow\n- **Dapple**\n    - Eskew",
            id="pairs",
        ),
        pytest.param(
            "Grouped and ungrouped",
            "- **Fallow**\n    - Gorse\n- **Hackle**\n    - Ingle\n"
            "- **Jessamy**\n    - Kestrel",
            id="mixed",
        ),
        pytest.param(
            "Several in a group",
            "- **Lanner**\n- **Merlin**\n    - Nimble\n    - Osier",
            id="several",
        ),
        pytest.param(
            "A summary list",
            "- **Pellet**\n    - Quillon\n    - [Change pellet](/change-pellet)\n"
            "- **Rookery**\n    - Sallow Tansy",
            id="summary-list",
        ),
        pytest.param(
            "A grouped list in a grouped description",
            "- **Umber**\n    - **Vetch**\n        - Wicket",
            id="nested",
        ),
        pytest.param(
            "A grouped list in a description",
            "- **Yarrow**\n    - **Zinnia**\n        - Alder",
            id="nested-in-a-plain-list",
        ),
        pytest.param(
            "A grouped term holding a picture",
            "-   Bracken map\n\n    <!-- image -->\n    - Cobble\n"
            "- **Dunnock**\n    - Eyrie",
            id="picture-term",
        ),
        pytest.param(
            "A grouped term holding a table",
            "-   | Ivy | Juniper |\n    | - | - |\n    | 1 | 2 |\n    - Kittiwake",
            id="table-term",
        ),
        pytest.param("A group in a group", "- **Linnet**\n    - Mallow", id="deeper"),
        pytest.param("A hidden group", "- **Plover**\n    - Quince", id="hidden"),
        pytest.param(
            "In a list item", "- Rowan\n    - **Sedge**\n        - Teasel", id="item"
        ),
        pytest.param(
            "In a table", "| Terms |\n| - |\n| - **Urchin**     - Vole |", id="cell"
        ),
    ],
)
def test_content_md_writes_each_group_s_terms_and_descriptions(
    result: Any, heading: str, markdown: str
) -> None:
    """What each section's list is in content.md, and in its chunk. A term
    holding only a picture or a table is written as P60 writes one in a list
    that is not grouped: it holds the picture, its description under it."""
    assert _section(result.markdown, heading) == markdown
    chunks = [seed.text for seed in result.chunk_seeds]
    assert markdown.replace("\n\n    <!-- image -->", "") in chunks


def test_commonmark_reads_each_group_as_the_list_s(result: Any) -> None:
    """Read back, a group is items of the one list: each term an item, its
    descriptions a list inside it, the next group's term the next item."""
    reader = MarkdownIt("commonmark").enable("table")
    assert reader.render(_section(result.markdown, "Grouped pairs")) == (
        "<ul>\n<li><strong>Brisk</strong>\n<ul>\n<li>Callow</li>\n</ul>\n</li>\n"
        "<li><strong>Dapple</strong>\n<ul>\n<li>Eskew</li>\n</ul>\n</li>\n</ul>\n"
    )
    assert reader.render(
        _section(result.markdown, "A grouped term holding a picture")
    ) == (
        "<ul>\n<li>\n<p>Bracken map</p>\n<!-- image -->\n<ul>\n<li>Cobble</li>\n"
        "</ul>\n</li>\n<li>\n<p><strong>Dunnock</strong></p>\n<ul>\n<li>Eyrie</li>\n"
        "</ul>\n</li>\n</ul>\n"
    )


def test_content_json_holds_each_group_s_terms_in_the_list(result: Any) -> None:
    """The grouped pairs are one description list whose items are the terms,
    each holding its descriptions, as docling records a list with no groups.
    A `<div>` leaves no group of its own in the record."""
    document = json.loads(result.document_json)
    by_ref = {item["self_ref"]: item for item in document["texts"]}
    by_ref.update({item["self_ref"]: item for item in document["groups"]})

    def described(term: dict[str, Any]) -> list[str]:
        return [
            by_ref[item["$ref"]]["text"]
            for group in term["children"]
            for item in by_ref[group["$ref"]]["children"]
        ]

    brisk = next(item for item in document["texts"] if item["text"] == "Brisk")
    listed = by_ref[brisk["parent"]["$ref"]]
    assert listed["name"] == "description list"
    terms = [by_ref[child["$ref"]] for child in listed["children"]]
    assert [(term["text"], described(term)) for term in terms] == [
        ("Brisk", ["Callow"]),
        ("Dapple", ["Eskew"]),
    ]
    assert all(term["formatting"]["bold"] for term in terms)
    assert len(document["pictures"]) == PICTURES
    docling_bridge.validate_document_json(result.document_json)


def test_every_picture_keeps_its_ocr_text(recognized: Any) -> None:
    """With the grouped pictures made, the backend's pictures and the
    snapshot's images agree, so every asset's recognized text is attached,
    a grouped term's inside the term. The nav's picture is chrome, so its
    asset is listed as one (docs/09 P57)."""
    assert recognized.warnings == []
    assert recognized.pictured_asset_ids == [
        f"asset-{n:03d}" for n in range(2, PICTURES + 1)
    ]
    assert recognized.chrome_pictured_asset_ids == ["asset-001"]
    assert _section(recognized.markdown, "A grouped term holding a picture").startswith(
        "-   Bracken map\n\n"
        "    [OCR text from asset-002, machine-recognized by Tesseract, "
        "mean confidence 90%:]\n    GLYPH2\n    [End of OCR text from asset-002]\n\n"
        "    <!-- image -->\n    - Cobble\n"
    )
    figures = [seed for seed in recognized.chunk_seeds if seed.kind == "figure"]
    assert len(figures) == PICTURES


def test_a_hidden_group_stays_out_and_a_nav_s_group_stays_chrome(result: Any) -> None:
    """A group docling reads as hidden is left grouped, so what it holds
    stays out of the record, as a hidden term does; the group after it is
    read. The nav's group, before the page's heading, is chrome."""
    surfaces = [result.document_json, result.markdown, result.text, result.doctags]
    surfaces += [seed.text for seed in result.chunk_seeds]
    for surface in surfaces:
        assert "Nettle" not in surface
        assert "Oxlip" not in surface
    assert "Plover" in result.markdown
    document = json.loads(result.document_json)
    nav_term = document["texts"][0]
    assert (nav_term["label"], nav_term["text"]) == ("list_item", "")
    assert nav_term["content_layer"] == "furniture"
    assert "Ledger" not in result.markdown
    assert result.markdown.startswith("# Description lists grouped in divs")


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


@pytest.mark.parametrize(
    ("page", "before"),
    [
        pytest.param(
            "<dl><div><dt><img src='a.png'></dt><dd>b</dd></div></dl>",
            "<dl><div><dt>",
            id="grouped",
        ),
        pytest.param(
            "<dl class='s'><div class='r'><div><dt class='k'>"
            "<table><tr><td>a</td></tr></table></dt></div></div></dl>",
            "<dl class='s'><div class='r'><div><dt class='k'>",
            id="deeper",
        ),
    ],
)
def test_a_grouped_term_holding_only_a_picture_is_marked(
    page: str, before: str
) -> None:
    """P60's stand-in reaches a grouped term, so the backend makes its item."""
    marked = mark_empty_items(page)
    assert marked.count(EMPTY_ITEM) == 1
    assert marked.index(EMPTY_ITEM) == len("\N{ZERO WIDTH NO-BREAK SPACE}" + before)


@pytest.mark.parametrize(
    "page",
    [
        pytest.param(
            "<dl><dt>a</dt><dd><div><dt><img src='a.png'></dt></div></dd></dl>",
            id="in-a-description",
        ),
        pytest.param(
            "<ul><li>a<div><dt><img src='a.png'></dt></div></li></ul>",
            id="in-a-list-item",
        ),
        pytest.param(
            "<div><div><dt><img src='a.png'></dt></div></div>", id="outside-a-list"
        ),
    ],
)
def test_a_term_a_div_holds_outside_a_description_list_is_not(page: str) -> None:
    """Past the `<div>`s, the term is in a description, a list item or no
    list at all, and docling does not read it as a term of a list."""
    assert mark_empty_items(page) is page


def test_a_page_carrying_the_stand_in_still_reads_its_groups() -> None:
    """A page with U+FDD2 of its own is not marked, but its groups are still
    taken away: a grouped term with text is read with its description."""
    page = (
        '<html><head><meta charset="utf-8"></head><body><h1>T</h1>'
        "<p>odd ﷒ kept</p><dl><div><dt>Brisk</dt><dd>Callow</dd></div></dl>"
        "</body></html>"
    )
    read = extract(page, [])
    assert "- **Brisk**\n    - Callow\n" in read.markdown
    assert any("U+FDD2" in warning for warning in read.warnings)


def test_the_backend_still_has_what_reading_groups_uses() -> None:
    """The methods `_reading_groups` overrides or calls are private to
    docling, so an upgrade that renames one is caught here rather than by a
    page."""
    from docling.backend import html_backend

    missing = [
        hook
        for hook in docling_bridge._GROUP_HOOKS
        if not hasattr(html_backend.HTMLDocumentBackend, hook)
    ]
    assert not missing


def test_without_those_methods_a_group_is_dropped_and_the_record_says_so(
    monkeypatch: Any,
) -> None:
    """With one gone, the page is read without the group reader, so a group
    is dropped again, as docling drops it, and the warning says so."""
    monkeypatch.setattr(
        docling_bridge,
        "_GROUP_HOOKS",
        (*docling_bridge._GROUP_HOOKS, "_a_method_docling_renamed"),
    )
    page = "<h1>Head</h1><dl><div><dt>Brisk</dt><dd>Callow</dd></div></dl><p>After</p>"
    result = extract(page, [])
    assert result.markdown == "# Head\n\nAfter\n"
    warning = next(w for w in result.warnings if "docs/09 P72" in w)
    assert "each such group is left out of every surface" in warning
