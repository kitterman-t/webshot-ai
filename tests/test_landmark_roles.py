"""Chrome marked by an ARIA landmark role stays out of the body (docs/09 P71).

The backend files everything before a page's first heading on the furniture
layer, and the bridge moves back to the body what is not page chrome: text by
whether it reads out of a header, nav, footer or aside region, a table or
picture by whether its element stands in one (P57). A page that marks its
chrome with a landmark role instead (`<div role="banner">`, a
`<table role="navigation">`) had that chrome read as content: its logo, menu
and disclaimer reached content.md, content.txt and chunks.jsonl.

An element with one of the four elements' roles, banner, navigation,
contentinfo or complementary, is now a chrome region for both tests. No
browser: each case calls `extract`.
"""

from __future__ import annotations

import json
from pathlib import Path

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

FIXTURE = Path(__file__).parent / "fixtures" / "landmark_roles.html"
HEADING = "Talvessa"

ASSET = VisualAsset(
    id="asset-001",
    file="assets/asset-001.png",
    kind="img",
    width=640,
    height=240,
    ocr=OCRResult(text="Recognized1", confidence=90.0, language="eng", engine="t"),
    sha256="ab" * 32,
)
IMG = (
    '<img src="assets/asset-001.png" alt="Orlenvale logo" '
    'data-webshot-asset-id="asset-001">'
)
#: Chrome as it may open a page: a logo, some text and a table of links.
CHROME_CONTENT = (
    f"<a href='/'>{IMG}</a><p>Orlenvale Park Service</p>"
    "<table><tr><td><a href='#a'>Brask trail</a></td></tr></table>"
)
#: Each landmark role, and the element it is the role of.
ROLES = {
    "banner": "header",
    "navigation": "nav",
    "contentinfo": "footer",
    "complementary": "aside",
}


def _page(markup: str) -> str:
    return f'<html lang="en"><head></head><body>{markup}</body></html>'


def _headed(markup: str) -> str:
    return _page(f"{markup}<h1>{HEADING}</h1><p>Body.</p>")


def _layers(result: ExtractionResult, kind: str) -> list[str]:
    return [item["content_layer"] for item in json.loads(result.document_json)[kind]]


def _chunk_texts(result: ExtractionResult) -> list[str]:
    return [record["text"] for record in build_chunk_records(result.chunk_seeds)]


@pytest.fixture(scope="module")
def page() -> ExtractionResult:
    return extract(FIXTURE.read_text(encoding="utf-8"), [ASSET])


def test_the_fixture_keeps_its_role_marked_chrome_out(page: ExtractionResult) -> None:
    validate_document_json(page.document_json)
    assert not page.warnings
    for words in (
        "Orlenvale logo",
        "Recognized1",
        "Orlenvale Park Service",
        "Plan a visit",
        "Learn about Velm",
        "does not imply endorsement",
        "Brask trail",
    ):
        assert words not in page.markdown, words
        assert words not in page.text, words
        assert not any(
            words in chunk
            for chunk in _chunk_texts(page)
            if not chunk.startswith("Visual asset ")
        ), words
    assert _layers(page, "tables") == ["furniture", "body"]
    assert _layers(page, "pictures") == ["furniture"]
    assert page.pictured_asset_ids == []
    assert page.chrome_pictured_asset_ids == ["asset-001"]
    # Still in the surfaces that carry furniture.
    assert "Brask trail" in page.doctags
    assert "Orlenvale Park Service" in page.doctags


def test_the_fixture_keeps_its_content(page: ExtractionResult) -> None:
    assert page.markdown == (
        "The Velm ridge rises above the valley.\n\n"
        "| Gauge | Millimetres |\n| - | - |\n| Brask | 41 |\n\n"
        f"# {HEADING}\n\nThe valley floods each autumn.\n"
    )
    for words in ("The Velm ridge rises above the valley.", "| Brask | 41 |"):
        assert words in page.text or words.strip("| ") in page.text, words
        assert any(words in chunk for chunk in _chunk_texts(page)), words


@pytest.mark.parametrize("role", ROLES, ids=ROLES)
@pytest.mark.parametrize("host", ["div", "section"])
def test_a_landmark_role_marks_chrome_as_its_element_does(role: str, host: str) -> None:
    """`<div role="banner">` above the first heading reads as `<header>` does."""
    element = ROLES[role]
    by_role = extract(
        _headed(f'<{host} role="{role}">{CHROME_CONTENT}</{host}>'), [ASSET]
    )
    by_element = extract(_headed(f"<{element}>{CHROME_CONTENT}</{element}>"), [ASSET])

    assert by_role.markdown == by_element.markdown == f"# {HEADING}\n\nBody.\n"
    assert by_role.text == by_element.text
    assert _chunk_texts(by_role) == _chunk_texts(by_element)
    for kind in ("texts", "tables", "pictures"):
        expected = _layers(by_element, kind)
        if element == "footer" and kind == "texts":
            # Everything the backend made inside a <footer> stays furniture
            # without a text test (P65, P58), the caption it makes of the
            # logo's alt text too. A role is no <footer> to the backend, so
            # that caption is tested by its text, which no region holds, and
            # goes to the body layer of content.json as a <header> logo's
            # does (P58-5); no surface writes a furniture picture's caption.
            texts = json.loads(by_element.document_json)["texts"]
            assert [item["label"] for item in texts].index("caption") == 0
            assert texts[0]["text"] == "Orlenvale logo"
            assert expected[0] == "furniture"
            expected = ["body", *expected[1:]]
        assert _layers(by_role, kind) == expected, kind
    assert by_role.pictured_asset_ids == by_element.pictured_asset_ids == []
    assert by_role.chrome_pictured_asset_ids == ["asset-001"]
    assert not by_role.warnings


@pytest.mark.parametrize("role", ["navigation", "Navigation", "navigation menubar"])
def test_a_table_given_a_landmark_role_is_its_own_region(role: str) -> None:
    result = extract(
        _headed(
            f'<table role="{role}"><tr><td><a href="#a">Brask trail</a></td>'
            "</tr></table>"
        ),
        [],
    )
    assert result.markdown == f"# {HEADING}\n\nBody.\n"
    assert _layers(result, "tables") == ["furniture"]


#: Roles that are not a page's chrome, on markup that is content.
NOT_CHROME = {
    "main": "main",
    "region": "region",
    "search": "search",
    "presentation": "presentation",
    "none": "none",
    # ARIA reads the first token it knows, and menubar is one.
    "a landmark as a fallback": "menubar navigation",
}


@pytest.mark.parametrize("role", NOT_CHROME.values(), ids=NOT_CHROME.keys())
def test_any_other_role_leaves_content_in_the_body(role: str) -> None:
    markup = (
        f'<div role="{role}">{IMG}<p>The Velm ridge.</p>'
        "<table><tr><td>Quellan</td></tr></table></div>"
    )
    result = extract(_headed(markup), [ASSET])
    alone = extract(_page(markup), [ASSET])

    assert result.markdown == f"{alone.markdown}\n# {HEADING}\n\nBody.\n"
    assert "The Velm ridge." in result.markdown
    assert _layers(result, "tables") == ["body"]
    assert _layers(result, "pictures") == ["body"]
    assert result.pictured_asset_ids == ["asset-001"]


def test_a_region_ends_at_its_own_end_tag() -> None:
    """A <div> inside a <div role> closes first; the region runs on to its own
    </div>, and content after it is content."""
    # The page is spelled plainly, so the cleaner P58 requires changes nothing.
    facts = _SnapshotFacts(clean=lambda text: text)
    facts.feed(
        _headed(
            '<div role="banner"><div><a href="/">Orlenvale</a></div>'
            f"<table><tr><td>Brask</td></tr></table>{IMG}</div>"
            f"<div><p>Velm lede.</p><table><tr><td>Quellan</td></tr></table>{IMG}</div>"
        )
    )
    assert facts.table_positions == [
        _Position(chrome=True, furniture=True),
        _Position(chrome=False, furniture=True),
    ]
    assert facts.image_positions == [
        _Position(chrome=True, furniture=True),
        _Position(chrome=False, furniture=True),
    ]
    assert facts.chrome_regions == ["Orlenvale Brask"]
    assert facts.is_chrome_text("Orlenvale")
    assert not facts.is_chrome_text("Velm lede.")


def test_a_region_closes_over_elements_left_open_inside_it() -> None:
    result = extract(
        _headed(
            '<div role="navigation"><ul><li>Plan a visit<li>Learn about Velm</ul>'
            "</div><p>The Velm ridge.</p>"
        ),
        [],
    )
    assert result.markdown == f"The Velm ridge.\n\n# {HEADING}\n\nBody.\n"


def test_an_empty_element_with_a_landmark_role_opens_no_region() -> None:
    """An <img> or <hr> has no end tag, so a region it opened would never
    close and everything after it would read as chrome."""
    # Positions and regions only: the cleaner P58 requires changes nothing.
    facts = _SnapshotFacts(clean=lambda text: text)
    facts.feed(
        _headed(
            f'<hr role="navigation">{IMG.replace("<img ", "<img role=banner ")}'
            "<p>The Velm ridge.</p><table><tr><td>Quellan</td></tr></table>"
        )
    )
    assert facts.image_positions == [_Position(chrome=False, furniture=True)]
    assert facts.table_positions == [_Position(chrome=False, furniture=True)]
    assert facts.chrome_regions == []


def test_a_landmark_below_the_first_heading_is_left_where_the_backend_filed_it() -> (
    None
):
    """The backend files a <div role="contentinfo"> below the heading on the
    body, as it does a <header> or <nav> there, and the bridge only moves
    furniture to the body: a role marks chrome only where the backend already
    filed furniture (P57-6)."""
    result = extract(
        _page(
            f"<h1>{HEADING}</h1><p>Body.</p>"
            '<div role="contentinfo"><p>Talvessa Trust imprint</p></div>'
        ),
        [],
    )
    assert result.markdown == f"# {HEADING}\n\nBody.\n\nTalvessa Trust imprint\n"
