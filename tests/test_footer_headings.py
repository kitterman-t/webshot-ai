"""A <footer> that holds a heading stays out of the reading surfaces (docs/09 P65).

docling's HTML backend files a footer's content on the furniture layer, which
the serializers and the chunker skip, but sets the body layer at every heading
it meets, inside a footer as anywhere, until the footer ends. A site footer's
column headings ("Company", "Resources") and everything after them reached
content.md, content.txt and chunks.jsonl, and the paragraph after the footer
was chunked under the footer's last heading.

What the page said inside and outside its footer is read off the source with
the standard library's parser, never off docling's model: the property is that
no footer text reaches a reading surface, so the list of footer text cannot
come from the component under test.
"""

from __future__ import annotations

import json
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import pytest

from webshot.capture.visuals import VisualAsset
from webshot.extract import docling_bridge
from webshot.extract.chunks import build_chunk_records
from webshot.extract.docling_bridge import ExtractionResult, extract
from webshot.ocr.tesseract import OCRResult

FIXTURES = Path(__file__).parent / "fixtures"


class _Texts(HTMLParser):
    """Each text node of a page, and whether it sits inside a <footer>."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.inside: list[str] = []
        self.outside: list[str] = []
        self._footers = 0
        self._head = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._footers += tag == "footer"
        self._head += tag == "head"
        alt = dict(attrs).get("alt")
        if tag == "img" and alt and self._footers:
            self.inside.append(alt)

    def handle_endtag(self, tag: str) -> None:
        self._footers -= tag == "footer" and self._footers > 0
        self._head -= tag == "head" and self._head > 0

    def handle_data(self, data: str) -> None:
        text = " ".join(data.split())
        if text and not self._head:
            (self.inside if self._footers else self.outside).append(text)


def _texts(page: str) -> _Texts:
    parser = _Texts()
    parser.feed(page)
    parser.close()
    assert parser.inside, "the page has no footer text to keep out"
    return parser


def _as_written(text: str) -> str:
    # The backend writes a curly apostrophe as a straight one.
    return text.replace("\u2019", "'")


def _surfaces(result: ExtractionResult) -> dict[str, str]:
    """The reading surfaces built from the document's layers.

    A figure chunk is not: WebShot writes one for every picture it matched to
    a harvested asset, chrome or not, under the heading the capture recorded
    beside the image (docs/04-spec.md §5.6, docs/09 P65-5).
    """
    records = build_chunk_records(result.chunk_seeds)
    return {
        "content.md": result.markdown,
        "content.txt": result.text,
        "chunks.jsonl": "\n".join(
            json.dumps(record, ensure_ascii=False)
            for record in records
            if record["meta"]["kind"] != "figure"
        ),
    }


def _leaks(page: str, result: ExtractionResult) -> list[tuple[str, str]]:
    """Each (surface, footer text) where a reading surface holds footer text."""
    return [
        (name, text)
        for name, surface in _surfaces(result).items()
        for text in _texts(page).inside
        if text in surface or _as_written(text) in surface
    ]


def _layers(result: ExtractionResult) -> dict[str, list[str]]:
    by_layer: dict[str, list[str]] = {}
    for item in json.loads(result.document_json)["texts"]:
        by_layer.setdefault(item["content_layer"], []).append(item["text"])
    return by_layer


def _badge() -> VisualAsset:
    return VisualAsset(
        id="asset-001",
        file="assets/asset-001.png",
        kind="image",
        width=88,
        height=31,
        alt="Badge",
        nearby_heading="Resources",
        ocr=OCRResult(
            text="FOOTER BADGE WORDS",
            confidence=91.0,
            language="eng",
            engine="Tesseract",
        ),
        sha256="cd" * 32,
    )


@pytest.fixture(scope="module")
def page() -> str:
    return (FIXTURES / "footer_headings.html").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def result(page: str) -> ExtractionResult:
    return extract(page, [_badge()])


def test_no_footer_text_reaches_a_reading_surface(
    page: str, result: ExtractionResult
) -> None:
    """A heading in the footer, one in a table cell, one in a <details>, one
    deeper than the page's own: before P65 each sent the rest of the footer
    to content.md, content.txt and chunks.jsonl."""
    assert _leaks(page, result) == []


def test_a_footer_pictures_recognized_text_stays_out_of_the_text(
    result: ExtractionResult,
) -> None:
    # The badge is attached, as page chrome: since P57 a picture outside the
    # body is counted apart from the body's (docs/09 P57-3).
    assert result.chrome_pictured_asset_ids == ["asset-001"]
    assert result.pictured_asset_ids == []
    assert "FOOTER BADGE WORDS" not in result.markdown
    assert "FOOTER BADGE WORDS" not in result.text


def test_the_body_layer_is_exactly_what_the_page_said_outside_its_footer(
    page: str, result: ExtractionResult
) -> None:
    assert _layers(result)["body"] == _texts(page).outside


def test_content_json_keeps_the_footer_on_the_furniture_layer(
    page: str, result: ExtractionResult
) -> None:
    """The footer is chrome, not lost: the lossless document keeps every line,
    headings as headings."""
    furniture = _layers(result)["furniture"]
    for text in _texts(page).inside:
        assert _as_written(text) in furniture
    headings = {
        item["text"]
        for item in json.loads(result.document_json)["texts"]
        if item["label"] == "section_header" and item["content_layer"] == "furniture"
    }
    assert headings == {"Company", "Resources", "Contact", "Privacy"}


def test_what_follows_the_footer_keeps_the_pages_own_section(
    result: ExtractionResult,
) -> None:
    """Before P65 the paragraph after the footer was chunked under the footer's
    last heading, ("Planting a spring garden", "Company", "Privacy")."""
    records = build_chunk_records(result.chunk_seeds)
    after = [
        r for r in records if r["text"].endswith("\nPrinted from the garden notes.")
    ]
    assert [r["meta"]["headings"] for r in after] == [
        ["Planting a spring garden", "Watering"]
    ]


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(
            "<h1>T</h1><p>Body.</p><footer><h2>Company</h2><p>About us</p></footer>",
            id="heading",  # the case as reported
        ),
        pytest.param(
            "<h1>T</h1><p>Body.</p><footer><h4>Company</h4><p>About us</p></footer>"
            "<p>After.</p>",
            id="deeper-heading",  # the backend opens a heading group in the footer
        ),
        pytest.param(
            "<h1>T</h1><p>Body.</p><footer><table><tr><td><h3>Company</h3>"
            "<p>Write to us.</p></td></tr></table><p>About us</p></footer>"
            "<p>After.</p>",
            id="table-cell",  # the heading is no parent of what follows it
        ),
        pytest.param(
            "<h1>T</h1><p>Body.</p><footer><details><summary>More</summary>"
            "<h3>Company</h3></details><p>About us</p></footer><p>After.</p>",
            id="details",
        ),
        pytest.param(
            "<h1>T</h1><p>Body.</p><footer><h2><img src='a.png' alt='Logo'></h2>"
            "<p>About us</p></footer><p>After.</p>",
            id="heading-image",  # the alt text became a caption at the top level
        ),
        pytest.param(
            "<h1>T</h1><article><p>Body.</p><footer><h1>Company</h1>"
            "<p>About us</p></footer></article><p>After.</p>",
            id="title-in-footer",
        ),
        pytest.param(
            "<p>Lede before any heading.</p><footer><h2>Company</h2>"
            "<p>About us</p></footer><p>After.</p>",
            id="only-heading-in-footer",
        ),
        pytest.param(
            "<h1>T</h1><p>Body.</p><footer><p>Top</p><footer><h3>Company</h3>"
            "</footer><p>About us</p></footer><p>After.</p>",
            id="nested-footers",
        ),
    ],
)
def test_every_shape_keeps_its_footer_out_and_the_rest_in(body: str) -> None:
    page = f"<html><head><title>t</title></head><body>{body}</body></html>"
    result = extract(page, [])
    assert _leaks(page, result) == []
    assert _layers(result)["body"] == _texts(page).outside
    assert all(text in result.markdown for text in _texts(page).outside)


def test_a_page_without_a_footer_heading_is_unchanged() -> None:
    """The text test still decides chrome before the first heading, and a
    footer without a heading was furniture before P65 too."""
    page = (
        "<html><head><title>t</title></head><body>"
        "<header>Site Chrome Header</header><p>A lede before the heading.</p>"
        "<h1>Head</h1><p>Body.</p><footer>Chrome Footer</footer></body></html>"
    )
    layers: dict[str, Any] = _layers(extract(page, []))
    assert layers["body"] == ["A lede before the heading.", "Head", "Body."]
    assert layers["furniture"] == ["Site Chrome Header", "Chrome Footer"]


def test_the_backend_still_has_the_method_the_bridge_extends() -> None:
    """`_use_footer` is docling's private method. A docling that renames it
    fails here, not in a capture."""
    from docling.backend import html_backend

    assert hasattr(html_backend.HTMLDocumentBackend, docling_bridge._FOOTER_HOOK)


def test_without_it_the_footer_is_read_as_docling_reads_it_and_the_record_says_so(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Absence must not read as success: with the method gone, the capture
    still completes on the backend without the override, and a page with a
    footer says what that costs. One without a footer says nothing."""
    monkeypatch.setattr(docling_bridge, "_FOOTER_HOOK", "_a_method_docling_renamed")
    page = (
        "<html><head><title>t</title></head><body><h1>T</h1><p>Body.</p>"
        "<footer><h2>Company</h2><p>About us</p></footer></body></html>"
    )
    result = extract(page, [])
    assert "## Company" in result.markdown.split("\n")
    assert [w for w in result.warnings if "docs/09 P65" in w]
    plain = extract("<html><body><h1>T</h1><p>Body.</p></body></html>", [])
    assert not [w for w in plain.warnings if "docs/09 P65" in w]
