"""Tables and pictures before the first heading reach the body (docs/09 P57).

The backend files everything before a page's first heading on the furniture
layer, which content.md, content.txt and the chunker skip, and the bridge
moved only text items back to the body. A table or picture there stayed
furniture: content.json and content.doctags held it, and none of content.md,
content.txt or chunks.jsonl did, nor the OCR text attached to the picture,
while `pictured_asset_ids` said that text had reached all three.

A table or picture has no text to tell page chrome by, so where its element
stands in the snapshot decides: one inside header/nav/footer/aside stays
furniture, any other reads as it does on a page with no heading at all. No
browser: each case calls `extract`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from webshot.capture.visuals import VisualAsset
from webshot.extract.chunks import build_chunk_records
from webshot.extract.docling_bridge import (
    ExtractionResult,
    _Position,
    _SnapshotFacts,
    extract,
    validate_document_json,
)
from webshot.ocr.tesseract import OCRResult

FIXTURE = Path(__file__).parent / "fixtures" / "pre_heading_blocks.html"
HEADING = "Talvessa"


def _asset(number: int) -> VisualAsset:
    asset_id = f"asset-{number:03d}"
    return VisualAsset(
        id=asset_id,
        file=f"assets/{asset_id}.png",
        kind="img",
        width=640,
        height=240,
        ocr=OCRResult(
            text=f"Recognized{number}", confidence=90.0, language="eng", engine="t"
        ),
        sha256="ab" * 32,
    )


ASSETS = [_asset(number) for number in range(1, 6)]


def _img(number: int, alt: str = "") -> str:
    return (
        f'<img src="assets/asset-{number:03d}.png" alt="{alt}" '
        f'data-webshot-asset-id="asset-{number:03d}">'
    )


def _page(markup: str) -> str:
    return f'<html lang="en"><head></head><body>{markup}</body></html>'


def _layers(result: ExtractionResult, kind: str) -> list[str]:
    return [item["content_layer"] for item in json.loads(result.document_json)[kind]]


def _chunk_texts(result: ExtractionResult, *, figures: bool = True) -> list[str]:
    return [
        record["text"]
        for record in build_chunk_records(result.chunk_seeds)
        if figures or record["meta"]["kind"] != "figure"
    ]


def _reading_opens(asset_id: str) -> str:
    return f"[OCR text from {asset_id}, machine-recognized"


@pytest.fixture(scope="module")
def page() -> ExtractionResult:
    return extract(FIXTURE.read_text(encoding="utf-8"), ASSETS)


def test_the_fixture_reaches_every_text_surface(page: ExtractionResult) -> None:
    validate_document_json(page.document_json)
    assert not page.warnings
    for words in (
        "Rainfall chart",
        "Recognized2",
        "Gauge readings",
        "| Brask | 41 |",
        "The Velm ridge.",
        "Recognized3",
        "Survey badge",
        "Recognized4",
        "| Quellan | Dry |",
    ):
        assert words in page.markdown, words
        assert words in page.text, words
        assert any(words in chunk for chunk in _chunk_texts(page)), words
    assert page.markdown.index("| Quellan | Dry |") < page.markdown.index(
        f"# {HEADING}"
    )


def test_the_fixture_keeps_its_chrome_out(page: ExtractionResult) -> None:
    for words in (
        "Orlenvale logo",
        "Recognized1",
        "Menu",
        "Partner badge",
        "Recognized5",
        "Imprint",
    ):
        assert words not in page.markdown, words
        assert words not in page.text, words
    # Header, lead and footer tables and pictures, in document order.
    assert _layers(page, "tables") == ["furniture", "body", "body", "furniture"]
    assert _layers(page, "pictures") == [
        "furniture",
        "body",
        "body",
        "body",
        "furniture",
    ]
    # Every table and picture is still in the surfaces that carry furniture.
    assert len(page.tables) == 4
    assert page.doctags.count("<picture>") == 5
    assert page.doctags.count("<otsl>") == 4


def test_pictured_asset_ids_says_where_the_text_went(page: ExtractionResult) -> None:
    assert page.pictured_asset_ids == ["asset-002", "asset-003", "asset-004"]
    assert page.chrome_pictured_asset_ids == ["asset-001", "asset-005"]
    chunks = _chunk_texts(page)
    for asset_id in page.pictured_asset_ids:
        opening = _reading_opens(asset_id)
        assert opening in page.markdown, asset_id
        assert opening in page.text, asset_id
        assert any(opening in chunk for chunk in chunks), asset_id
    for asset_id in page.chrome_pictured_asset_ids:
        opening = _reading_opens(asset_id)
        assert opening not in page.markdown, asset_id
        assert opening not in page.text, asset_id
        # Its figure chunk, which every attached picture has.
        assert any(
            chunk.startswith(f"Visual asset {asset_id}") and opening in chunk
            for chunk in chunks
        ), asset_id


#: Tables and pictures as they may open a page, before any heading.
CONTENT = {
    "picture": _img(1, "Rainfall chart"),
    "picture without alt text": _img(1),
    "table": "<table><tr><td>Brask</td><td>41</td></tr></table>",
    "table with a caption and header row": (
        "<table><caption>Gauge readings</caption><tr><th>Gauge</th><th>mm</th>"
        "</tr><tr><td>Brask</td><td>41</td></tr></table>"
    ),
    "figure with a caption": (
        f"<figure>{_img(1, 'Ridge map')}<figcaption>The Velm ridge.</figcaption>"
        "</figure>"
    ),
    "linked picture in a paragraph": (
        f'<p><a href="#b">{_img(1, "Badge")}</a> Surveyed in spring.</p>'
    ),
    "table in a div": "<div><table><tr><td>Quellan</td></tr></table></div>",
    "picture and table in a section": (
        f"<section>{_img(1, 'Chart')}<table><tr><td>Velm</td></tr></table></section>"
    ),
    "table holding a picture": (
        f"<table><tr><td>{_img(1, 'Cell chart')}</td><td>Brask</td></tr></table>"
    ),
    "two pictures and a table": (
        f"{_img(1, 'First')}<table><tr><td>Middle</td></tr></table>{_img(2, 'Last')}"
    ),
}


@pytest.mark.parametrize("markup", CONTENT.values(), ids=CONTENT.keys())
def test_a_table_or_picture_before_the_first_heading_reads_as_on_a_page_with_none(
    markup: str,
) -> None:
    before = extract(_page(f"{markup}<h1>{HEADING}</h1><p>Body.</p>"), ASSETS)
    alone = extract(_page(markup), ASSETS)

    assert before.markdown == f"{alone.markdown}\n# {HEADING}\n\nBody.\n"
    assert before.text == f"{alone.text}\n{HEADING}\n\nBody.\n"
    # The figure chunks come after the chunker's, as they do on every page.
    assert _chunk_texts(before, figures=False) == [
        *_chunk_texts(alone, figures=False),
        f"{HEADING}\nBody.",
    ]
    assert [seed for seed in before.chunk_seeds if seed.kind == "figure"] == [
        seed for seed in alone.chunk_seeds if seed.kind == "figure"
    ]
    assert before.pictured_asset_ids == alone.pictured_asset_ids
    assert before.chrome_pictured_asset_ids == []
    assert set(_layers(before, "tables")) <= {"body"}
    assert set(_layers(before, "pictures")) <= {"body"}
    assert not before.warnings


@pytest.mark.parametrize(
    ("markup", "written", "pictured"),
    [
        (
            f"<ul><li>Evaluate the programs {_img(1, 'Show menu')}</li>"
            "<li>Next</li></ul>",
            ["Show menu", _reading_opens("asset-001")],
            ["asset-001"],
        ),
        (
            "<ol><li>Holds a table<table><tr><td>Brask</td></tr></table></li></ol>",
            ["| Brask |"],
            [],
        ),
    ],
    ids=["picture in a list item", "table in a list item"],
)
def test_a_table_or_picture_in_a_list_item_before_the_first_heading_is_written(
    markup: str, written: list[str], pictured: list[str]
) -> None:
    """How the list itself is written is P53's to decide; the picture or table
    under an item moves by its own position, whatever its parent."""
    result = extract(_page(f"{markup}<h1>{HEADING}</h1><p>Body.</p>"), ASSETS)

    assert set(_layers(result, "tables")) <= {"body"}
    assert set(_layers(result, "pictures")) <= {"body"}
    chunks = _chunk_texts(result, figures=False)
    for words in written:
        assert words in result.markdown, words
        assert words in result.text, words
        assert any(words in chunk for chunk in chunks), words
    assert result.pictured_asset_ids == pictured


#: Page chrome that opens a page with a picture or table in it.
CHROME = {
    tag: f"<{tag}>{_img(1, 'Logo')}<table><tr><td>Menu</td></tr></table></{tag}>"
    for tag in ("header", "nav", "footer", "aside")
}
CHROME["picture nested in a chrome div"] = (
    f"<header><div><a href='/'>{_img(1, 'Logo')}</a></div></header>"
)


@pytest.mark.parametrize("markup", CHROME.values(), ids=CHROME.keys())
def test_a_table_or_picture_in_page_chrome_stays_out(markup: str) -> None:
    result = extract(_page(f"{markup}<h1>{HEADING}</h1><p>Body.</p>"), ASSETS)

    assert result.markdown == f"# {HEADING}\n\nBody.\n"
    assert result.text == f"{HEADING}\n\nBody.\n"
    assert set(_layers(result, "tables")) <= {"furniture"}
    assert _layers(result, "pictures") == ["furniture"]
    assert result.pictured_asset_ids == []
    assert result.chrome_pictured_asset_ids == ["asset-001"]
    assert not result.warnings


def test_a_footer_after_the_heading_stays_out() -> None:
    result = extract(
        _page(
            f"<h1>{HEADING}</h1><p>Body.</p><footer>{_img(1, 'Badge')}"
            "<table><tr><td>Imprint</td></tr></table></footer>"
        ),
        ASSETS,
    )
    assert result.markdown == f"# {HEADING}\n\nBody.\n"
    assert _layers(result, "tables") == ["furniture"]
    assert result.chrome_pictured_asset_ids == ["asset-001"]


def test_counts_that_disagree_still_decide_a_one_sided_lead() -> None:
    """An image inside <pre> yields no picture, so the pairing fails; every
    image above the heading is content, so every furniture picture is too."""
    result = extract(
        _page(
            f"<pre>code {_img(1, 'In code')}</pre>{_img(2, 'Lead chart')}"
            f"<h1>{HEADING}</h1><p>Body.</p>"
        ),
        ASSETS,
    )
    assert _layers(result, "pictures") == ["body"]
    assert "Lead chart" in result.markdown
    assert not any("P57" in warning for warning in result.warnings)


def test_counts_that_disagree_leave_chrome_alone_without_a_warning() -> None:
    """Only the images where the backend files furniture decide: the logo is
    the only one above the heading, so a mismatch later says nothing new."""
    result = extract(
        _page(
            f"<header>{_img(1, 'Logo')}</header><h1>{HEADING}</h1><p>Body.</p>"
            f"<pre>code {_img(2, 'In code')}</pre>{_img(3, 'Body chart')}"
        ),
        ASSETS,
    )
    assert _layers(result, "pictures") == ["furniture", "body"]
    assert "Logo" not in result.markdown
    assert "Body chart" in result.markdown
    assert not any("P57" in warning for warning in result.warnings)


def test_counts_that_disagree_over_chrome_and_content_say_so() -> None:
    result = extract(
        _page(
            f"<header>{_img(1, 'Logo')}</header><pre>code {_img(2, 'In code')}</pre>"
            f"{_img(3, 'Lead chart')}<h1>{HEADING}</h1><p>Body.</p>"
        ),
        ASSETS,
    )
    # Neither is guessed at: both stay where the backend filed them.
    assert _layers(result, "pictures") == ["furniture", "furniture"]
    [warning] = [warning for warning in result.warnings if "P57" in warning]
    assert warning.startswith(
        "2 picture(s) above the page's first heading or in a footer may be "
        "missing from content.md, content.txt and chunks.jsonl: docling "
        "produced 2 picture(s) for 3 in the snapshot"
    )


def test_a_heading_inside_a_table_does_not_end_the_lead() -> None:
    """The backend starts the body at the first heading outside a table."""
    facts = _SnapshotFacts()
    facts.feed(
        _page(
            "<table><tr><td><h2>In a cell</h2></td></tr></table>"
            f"{_img(1)}<header>{_img(2)}</header><h1>{HEADING}</h1>{_img(3)}"
            f"<footer>{_img(4)}</footer><aside>{_img(5)}</aside>"
        )
    )
    assert facts.table_positions == [_Position(chrome=False, furniture=True)]
    assert facts.image_positions == [
        _Position(chrome=False, furniture=True),
        _Position(chrome=True, furniture=True),
        _Position(chrome=False, furniture=False),
        _Position(chrome=True, furniture=True),
        _Position(chrome=True, furniture=False),
    ]


def _nodes(result: ExtractionResult) -> dict[str, Any]:
    return json.loads(result.document_json)


def test_nothing_after_the_first_heading_changes() -> None:
    """The fixture's tail, from the heading on, reads the same with the lead
    removed: the pass moves furniture to the body and never the reverse."""
    markup = FIXTURE.read_text(encoding="utf-8")
    tail = markup[: markup.index("<body>") + 6] + markup[markup.index("<h1>") :]
    whole = extract(markup, ASSETS)
    alone = extract(tail, ASSETS)
    assert whole.markdown.endswith(alone.markdown)
    assert whole.text.endswith(alone.text)
    assert [item["content_layer"] for item in _nodes(alone)["tables"]] == _layers(
        whole, "tables"
    )[-1:]
