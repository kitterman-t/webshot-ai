"""Bridge contract tests: docling in, WebShot's types out (docs/06 pyramid).

Real library, tiny inputs, no browser and no network.  These pin the behaviors
the bridge exists to guarantee — the ones docs/09's spikes and Phase 2's
corrections found do NOT come free from the backend: single title, matched
pictures with OCR annotations, re-attached table captions, furniture reflow,
doctags surviving exotic code languages, every heading reaching a chunk, and
warned degradation (never a guess, never a failed capture) when the backend
skips or merges elements the snapshot parser counts.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from webshot.capture.visuals import VisualAsset
from webshot.extract.chunks import build_chunk_records
from webshot.extract.docling_bridge import (
    _captions_missing,
    extract,
    validate_document_json,
)
from webshot.ocr.tesseract import OCRResult

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Tag Title</title></head><body>
<h1>Real Heading</h1>
<p>Intro paragraph with enough words to chunk.</p>
<h2>Terms</h2>
<dl><dt>Capture</dt><dd>The act of recording.</dd></dl>
<h2>Numbers</h2>
<table><caption>Quarterly numbers</caption>
<thead><tr><th>Quarter</th><th>Value</th></tr></thead>
<tbody><tr><td>Q1</td><td>1.2</td></tr></tbody></table>
<figure><img src="assets/asset-001.png" alt="Chart" data-webshot-asset-id="asset-001">
<figcaption>Revenue chart.</figcaption></figure>
</body></html>"""


def _asset(
    asset_id: str = "asset-001", ocr_text: str = "SENTINEL WORDS"
) -> VisualAsset:
    return VisualAsset(
        id=asset_id,
        file=f"assets/{asset_id}.png",
        kind="canvas",
        width=640,
        height=240,
        caption="Revenue chart.",
        nearby_heading="Numbers",
        ocr=OCRResult(
            text=ocr_text, confidence=96.5, language="eng", engine="Tesseract"
        ),
        sha256="ab" * 32,
    )


@pytest.fixture(scope="module")
def result():
    return extract(PAGE, [_asset()])


def test_content_json_loads_as_docling_document(result) -> None:
    validate_document_json(result.document_json)  # acceptance G3


def test_the_title_tag_does_not_become_a_second_heading(result) -> None:
    document = json.loads(result.document_json)
    titles = [t["text"] for t in document["texts"] if t["label"] == "title"]
    assert titles == ["Real Heading"]
    assert "Tag Title" not in result.markdown


def test_picture_carries_ocr_annotations(result) -> None:
    document = json.loads(result.document_json)
    (picture,) = document["pictures"]
    kinds = {annotation["kind"] for annotation in picture["annotations"]}
    assert kinds == {"misc", "description"}
    description = next(a for a in picture["annotations"] if a["kind"] == "description")
    assert description["text"] == "SENTINEL WORDS"
    misc = next(a for a in picture["annotations"] if a["kind"] == "misc")
    record = misc["content"]["webshot"]
    assert record["asset_id"] == "asset-001"
    assert record["ocr"]["confidence"] == 96.5
    assert record["ocr"]["language"] == "eng"


def test_ocr_text_reaches_markdown_and_text_exports(result) -> None:
    assert "SENTINEL WORDS" in result.markdown
    assert "SENTINEL WORDS" in result.text


def test_table_caption_is_reattached(result) -> None:
    document = json.loads(result.document_json)
    (table,) = document["tables"]
    assert table["captions"], "the <caption> the backend drops must be re-attached"
    texts = {t["self_ref"]: t["text"] for t in document["texts"]}
    assert texts[table["captions"][0]["$ref"]] == "Quarterly numbers"


def test_tables_export_as_full_grids(result) -> None:
    assert result.tables == [[["Quarter", "Value"], ["Q1", "1.2"]]]


def test_definition_list_text_is_modeled_natively(result) -> None:
    assert "Capture" in result.text and "The act of recording." in result.text


def test_chunks_carry_provenance_and_a_figure_chunk(result) -> None:
    records = build_chunk_records(result.chunk_seeds)
    assert records, "no chunks at all"
    refs = {ref for record in records for ref in record["meta"]["doc_items"]}
    document = json.loads(result.document_json)
    known = {t["self_ref"] for t in document["texts"]}
    known |= {t["self_ref"] for t in document["tables"]}
    known |= {p["self_ref"] for p in document["pictures"]}
    assert refs <= known, f"unresolvable chunk refs: {refs - known}"
    figure_records = [r for r in records if r["meta"]["kind"] == "figure"]
    assert figure_records and "SENTINEL WORDS" in figure_records[0]["text"]
    assert all(record["meta"]["page"] is None for record in records)
    assert all(
        record["meta"]["locator"] == record["meta"]["doc_items"][0]
        for record in records
    )


def test_furniture_reflow_keeps_content_and_skips_chrome() -> None:
    page = (
        "<html><head><title>T</title></head><body>"
        "<header>Site Chrome Header</header><nav>Menu Items Here</nav>"
        "<p>A lede paragraph before the heading.</p><h1>Head</h1><p>Body.</p>"
        "<footer>Chrome Footer</footer></body></html>"
    )
    result = extract(page, [])
    assert "A lede paragraph before the heading." in result.text
    assert "Site Chrome Header" not in result.text
    assert "Chrome Footer" not in result.text
    # ...but the chrome is still in the lossless document, on its own layer.
    document = json.loads(result.document_json)
    furniture = [
        t["text"] for t in document["texts"] if t.get("content_layer") == "furniture"
    ]
    assert "Site Chrome Header" in furniture


def test_doctags_survive_code_languages_the_token_set_cannot_spell() -> None:
    page = (
        "<html><head></head><body><h1>T</h1>"
        '<pre><code class="language-json">{"a": 1}</code></pre></body></html>'
    )
    result = extract(page, [])
    assert "<doctag>" in result.doctags
    # content.json keeps the detected language even though doctags cannot.
    document = json.loads(result.document_json)
    code = next(t for t in document["texts"] if t["label"] == "code")
    assert code["code_language"] == "JSON"


def test_image_inside_pre_degrades_with_a_warning_not_a_failure() -> None:
    """docling produces no PictureItem for an <img> inside <pre>, so the
    positional mapping would be a guess.  The capture must still succeed:
    annotations are skipped, the warning says so, and the full OCR records
    stay in assets.json (found by the Phase 2 review — the original hard
    count check failed the whole capture)."""
    page = (
        "<html><head></head><body><h1>T</h1>"
        '<pre>log excerpt <img src="assets/asset-001.png" data-webshot-asset-id="asset-001"></pre>'
        '<img src="assets/asset-002.png" alt="Chart" data-webshot-asset-id="asset-002">'
        "</body></html>"
    )
    result = extract(page, [_asset(), _asset("asset-002")])
    assert result.warnings and "assets.json" in result.warnings[0]
    document = json.loads(result.document_json)
    for picture in document["pictures"]:
        assert picture["annotations"] == []  # nothing guessed
    assert not [
        r
        for r in build_chunk_records(result.chunk_seeds)
        if r["meta"]["kind"] == "figure"
    ]


def test_nested_tables_keep_their_caption_without_a_false_warning() -> None:
    """docling flattens a table nested in a cell to one TableItem while the
    snapshot parser counts two <table> tags, so no caption can be placed by
    position — and the capture must not fail over it (Phase 2 review).

    From docling-slim 2.130 the backend keeps `<caption>` itself, so the
    caption survives the flattening. A warning that it "could not be
    re-attached" would then report a loss that did not happen (P10-19)."""
    page = (
        "<html><head></head><body><h1>T</h1>"
        "<table><caption>Outer</caption><tr><td>"
        "<table><tr><td>inner</td></tr></table>"
        "</td></tr></table></body></html>"
    )
    result = extract(page, [])
    assert not any("caption" in warning.lower() for warning in result.warnings)
    document = json.loads(result.document_json)
    carried = {
        document["texts"][int(ref["$ref"].rsplit("/", 1)[1])]["text"]
        for table in document["tables"]
        for ref in table["captions"]
    }
    assert carried == {"Outer"}


def test_a_caption_no_table_carries_is_the_one_reported_missing() -> None:
    """What the count-mismatch warning is decided on: every snapshot caption
    that no table carries, compared as collapsed text and counted, so a second
    table captioned alike is not satisfied by the first one's caption."""
    assert _captions_missing(["O", "", "I2"], ["O"]) == ["I2"]
    assert _captions_missing(["A", "A"], ["A"]) == ["A"]
    assert _captions_missing(["  Two   words "], ["Two words"]) == []
    assert _captions_missing(["", ""], []) == []


def test_aligned_counts_match_means_no_warnings() -> None:
    result = extract(PAGE, [_asset()])
    assert result.warnings == []


def test_unmatched_pictures_are_tolerated_without_assets() -> None:
    page = (
        "<html><head></head><body><h1>T</h1>"
        '<img src="somewhere.png" alt="Unharvested"></body></html>'
    )
    result = extract(page, [])
    assert result.picture_count == 1  # picture exists, no annotation attached
    document = json.loads(result.document_json)
    assert document["pictures"][0]["annotations"] == []


FIXTURES = Path(__file__).parent / "fixtures"

#: Headings are read off the source HTML, not off docling's model: the property
#: is that what the page said reaches a chunk, so the list of what it said
#: cannot come from the component under test.
_HEADING_RE = re.compile(r"<h([1-6])>(.*?)</h\1>", re.DOTALL)


def _headings_missing_from(page: str, records: list[dict[str, Any]]) -> list[str]:
    return [
        heading
        for _, heading in _HEADING_RE.findall(page)
        if not any(
            heading in record["meta"]["headings"]
            or heading in record["text"].splitlines()
            for record in records
        )
    ]


def _heading_only_records_anchor_at_their_heading(
    result: Any, records: list[dict[str, Any]]
) -> None:
    by_ref = {t["self_ref"]: t for t in json.loads(result.document_json)["texts"]}
    for record in records:
        headings = record["meta"]["headings"]
        if record["text"] != "\n".join(headings):
            continue
        anchored = by_ref[record["meta"]["locator"]]
        assert record["meta"]["doc_items"] == [record["meta"]["locator"]]
        assert anchored["label"] in {"title", "section_header"}
        assert anchored["text"] == headings[-1]


def test_a_heading_whose_section_has_no_body_reaches_a_chunk() -> None:
    """P14-45: an h3 followed directly by an h2 left "Question 1" a section
    with no body, and the chunker dropped the heading -- content.md and
    content.txt kept it, chunks.jsonl never said it."""
    page = (FIXTURES / "empty_section_heading.html").read_text(encoding="utf-8")
    result = extract(page, [])
    records = build_chunk_records(result.chunk_seeds)
    assert _headings_missing_from(page, records) == []
    assert [record["text"] for record in records] == [
        "Question 1",
        "Did you complete this activity?\nYes, I completed the activity.",
    ]
    _heading_only_records_anchor_at_their_heading(result, records)


@pytest.mark.parametrize(
    "body",
    [
        pytest.param("<h1>T</h1><h2>Empty</h2><h2>Next</h2><p>n</p>", id="sibling"),
        pytest.param("<h1>T</h1><p>x</p><h2>Last</h2>", id="ending-the-page"),
        pytest.param(
            "<h1>T</h1><h2>Pictured</h2><img src='a.png'><h2>Next</h2><p>n</p>",
            id="picture-only",  # a picture serializes to no chunk text
        ),
        pytest.param(
            "<h1>T</h1><h2>A</h2><h3>B</h3><h2>C</h2><p>c</p>", id="nested-empty"
        ),
        pytest.param("<h1>Only</h1>", id="headings-only"),
    ],
)
def test_every_heading_reaches_a_chunk(body: str) -> None:
    """Every shape that leaves a heading's section empty, not only P14-45's:
    the heading's own chunk anchors at the heading, not at the top of its
    path, so a chunk under a title still points at the heading it is for."""
    page = f"<html><head></head><body>{body}</body></html>"
    result = extract(page, [])
    records = build_chunk_records(result.chunk_seeds)
    assert _headings_missing_from(page, records) == []
    _heading_only_records_anchor_at_their_heading(result, records)
