"""Visuals inside shadow roots and read frames are harvested (docs/09 P16-9).

The harvest looked in the top document only. An image, chart or canvas in an
open shadow root or in a frame the page can script was in the PDF, and its alt
text was in the snapshot, but it had no asset, no OCR and no warning.
`shadow_frame_visuals_page.html` puts one visual in each place, each drawing a
capitalized phrase for OCR to read.

The heading every stage reports, and the scroll positions the harvest moves,
are here too (docs/09 P16-12): both are facts about the same reading order.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any

import pytest
from playwright.async_api import async_playwright
from pypdf import PdfReader

from webshot.capture.prepare import BASE_PRINT_CSS
from webshot.capture.snapshot import FLAT_TREE_JS, HEADING_JS
from webshot.capture.visuals import VisualAsset, capture_visual_assets
from webshot.cli import main
from webshot.ocr.engine import OCRResult
from webshot.ocr.tesseract import tesseract_executable

FIXTURE = (
    Path(__file__).resolve().parent / "fixtures" / "shadow_frame_visuals_page.html"
)
SCROLLED = FIXTURE.with_name("scrolled_visuals_page.html")

#: Every visual the page shows, by label, in reading order, with the heading a
#: reader last passed before it. The light chart follows the card, so its
#: heading is the shadow root's, which document order never reached.
SHOWN = {
    "Shadow chart": ("svg", "Shadow section"),
    "Nested canvas": ("canvas", "Shadow section"),
    "Light chart": ("svg", "Shadow section"),
    "Figure frame": ("iframe", "Frame section"),
    "Framed chart": ("svg", "Frame section"),
    "Below the fold": ("img", "Frame section"),
}

#: The capitalized phrase each one draws, which is how the test knows OCR read
#: that visual and not a neighbour.
DRAWN = {
    "Shadow chart": "SHADOW",
    "Nested canvas": "NESTED",
    "Light chart": "LIGHT",
    "Framed chart": "FRAMED",
    "Below the fold": "FOLD",
}


def _flat(text: str) -> str:
    return " ".join(text.split())


def _attribute(tag: str, name: str) -> str:
    found = re.search(rf'{name}="([^"]*)"', tag)
    assert found, (name, tag)
    return found.group(1)


def _label(asset: dict[str, Any]) -> str:
    return str(asset["aria_label"] or asset["alt"] or asset["title"])


def _run(directory: Path, *options: str) -> dict[str, Any]:
    output = directory / "capture.pdf"
    report = directory / "report.json"
    code = main(
        [str(FIXTURE), "--output", str(output), "--report", str(report), *options]
    )
    assert code == 0
    bundle = output.with_suffix(".ai")
    records = json.loads((bundle / "assets.json").read_text("utf-8"))
    return {
        "bundle": bundle,
        "assets": {_label(asset): asset for asset in records["visual_assets"]},
        "ordered": [_label(asset) for asset in records["visual_assets"]],
        "report": json.loads(report.read_text("utf-8")),
        "pdf": _flat(
            "\n".join(p.extract_text() or "" for p in PdfReader(output).pages)
        ),
    }


@pytest.fixture(scope="module")
def capture(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    return _run(tmp_path_factory.mktemp("shadow-frame-visuals"), "--no-ocr")


@pytest.fixture(scope="module")
def recognized(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    """`--require-ocr`: an OCR that did not run fails the capture."""
    return _run(
        tmp_path_factory.mktemp("shadow-frame-visuals-ocr"),
        "--ocr-engine",
        "tesseract",
        "--require-ocr",
    )


@pytest.mark.browser
def test_every_visual_the_page_shows_is_an_asset_in_reading_order(
    capture: dict[str, Any],
) -> None:
    """The hidden frame's chart is not shown, and is not an asset."""
    assert capture["ordered"] == list(SHOWN)
    for label, (kind, heading) in SHOWN.items():
        asset = capture["assets"][label]
        assert (asset["kind"], asset["nearby_heading"]) == (kind, heading), asset
        assert asset["width"] >= 32 and asset["height"] >= 24, asset


@pytest.mark.browser
def test_each_visual_stands_in_the_snapshot_as_its_asset(
    capture: dict[str, Any],
) -> None:
    """Including the frame's image, whose page URL meant nothing in a bundle."""
    html = (capture["bundle"] / "content.html").read_text("utf-8")
    sources = {
        _attribute(tag, "data-webshot-asset-id"): _attribute(tag, "src")
        for tag in re.findall(r"<img [^>]*data-webshot-asset-id[^>]*>", html)
    }
    pictured = {a["id"] for a in capture["assets"].values() if a["kind"] != "iframe"}
    assert sources == {asset_id: f"assets/{asset_id}.png" for asset_id in pictured}
    assert "data:image" not in html
    assert "<svg" not in html and "<canvas" not in html


@pytest.mark.browser
def test_a_page_authored_id_in_a_frame_does_not_survive_the_harvest(
    capture: dict[str, Any],
) -> None:
    """The framed chart arrives carrying `asset-001`. The harvest cleans the
    frame's document before numbering, so asset-001 is the first visual in
    reading order, and the chart has its own id."""
    assets = capture["assets"]
    assert assets["Shadow chart"]["id"] == "asset-001"
    assert assets["Framed chart"]["id"] != "asset-001"
    html = (capture["bundle"] / "content.html").read_text("utf-8")
    assert html.count('data-webshot-asset-id="asset-001"') == 1


@pytest.mark.browser
def test_a_frame_with_a_visual_below_its_fold_prints_whole(
    capture: dict[str, Any],
) -> None:
    """The frame is grown to its document before the harvest (P16-16), so both
    of its sentences print and photographing its image scrolls nothing. The
    snapshot reads the frame whole either way."""
    assert "Top of the frame: printed where it starts." in capture["pdf"]
    assert "Bottom of the frame: printed only if it is scrolled." in capture["pdf"]
    markdown = _flat((capture["bundle"] / "content.md").read_text("utf-8"))
    assert "Top of the frame" in markdown and "Bottom of the frame" in markdown


@pytest.mark.browser
def test_a_restored_frame_raises_no_warning(capture: dict[str, Any]) -> None:
    assert not [w for w in capture["report"]["warnings"] if "may print scrolled" in w]


@pytest.mark.browser
@pytest.mark.skipif(tesseract_executable() is None, reason="tesseract is not installed")
@pytest.mark.parametrize("surface", ["content.md", "content.txt", "chunks.jsonl"])
def test_what_ocr_read_in_each_visual_reaches_the_text_surfaces(
    recognized: dict[str, Any], surface: str
) -> None:
    """Asserted as each asset's own OCR text, not this machine's (P7-13)."""
    text = _flat((recognized["bundle"] / surface).read_text("utf-8"))
    for label, word in DRAWN.items():
        asset = recognized["assets"][label]
        read = _flat(asset["ocr"]["text"])
        # The derivation proves nothing about an empty or misplaced read.
        assert word in read.upper(), (label, asset["ocr"])
        assert read in text, (label, surface)


class _Stub:
    """An OCR engine that always reads the same words, so the page is the
    only variable."""

    name = "stub"
    label = "Stub"

    def applied_settings(self, *, language: str, psm: int) -> dict[str, object]:
        return {}

    def unavailable_reason(self, *, language: str = "") -> str | None:
        return None

    async def recognize(self, image: Path, *, language: str, psm: int) -> OCRResult:
        return OCRResult(
            text="stub words", confidence=99.0, language=language, engine="Stub"
        )


#: Every recognized-text span, wherever it went, with the style it computes to.
_SPANS_JS = """() => {
    const spans = [];
    const visit = root => {
        for (const element of root.querySelectorAll('*')) {
            if (element.matches('[data-webshot-ocr-text]')) {
                const style = element.ownerDocument.defaultView.getComputedStyle(element);
                spans.push({
                    tree: root === document ? 'top' : root.nodeType === 11 ? 'shadow' : 'frame',
                    position: style.position,
                    fontSize: style.fontSize,
                });
            }
            if (element.shadowRoot) visit(element.shadowRoot);
            if (element.localName === 'iframe') {
                try { if (element.contentDocument) visit(element.contentDocument); } catch {}
            }
        }
    };
    visit(document);
    return spans;
}"""


@pytest.mark.browser
def test_recognized_text_stays_off_the_page_in_every_tree(tmp_path: Path) -> None:
    """The print stylesheet that keeps it in the text layer only is the top
    document's. Without the inline style, the words printed in full-size text
    beside each shadow-root and frame visual."""

    async def run() -> list[dict[str, str]]:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            try:
                page = await browser.new_page()
                await page.goto(FIXTURE.as_uri(), wait_until="load")
                await page.add_style_tag(content=BASE_PRINT_CSS)
                await capture_visual_assets(
                    page,
                    tmp_path / "assets",
                    max_assets=50,
                    ocr=True,
                    ocr_language="eng",
                    ocr_psm=11,
                    engine=_Stub(),
                )
                spans: list[dict[str, str]] = await page.evaluate(_SPANS_JS)
                return spans
            finally:
                await browser.close()

    spans = asyncio.run(run())
    # One per visual, in all three kinds of tree, or the style check is empty.
    assert len(spans) == len(SHOWN), spans
    assert {span["tree"] for span in spans} == {"top", "shadow", "frame"}
    assert all(
        (span["position"], span["fontSize"]) == ("absolute", "0.1px") for span in spans
    ), spans


async def _harvest(page: Any, directory: Path) -> list[VisualAsset]:
    assets, _, _ = await capture_visual_assets(
        page,
        directory / "assets",
        max_assets=50,
        ocr=True,
        ocr_language="eng",
        ocr_psm=11,
        engine=_Stub(),
    )
    return assets


def _in_browser(url: str, body: Any) -> Any:
    """Run `body(page)` on a fresh page at `url` and return what it returns."""

    async def run() -> Any:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            try:
                page = await browser.new_page()
                await page.goto(url, wait_until="load")
                return await body(page)
            finally:
                await browser.close()

    return asyncio.run(run())


# --------------------------------------------------------------------------- #
# HEADING_JS: the heading every stage reports
# --------------------------------------------------------------------------- #

#: Each probe names an element from the top document, and the heading a reader
#: last passed before reaching it.
_HEADING_PROBES_JS = """() => {
    const heading = __HEADING__;
    const card = document.getElementById('card').shadowRoot;
    const frame = document.querySelector('iframe[title="Figure frame"]');
    const probe = element => heading(element);
    return {
        h1: probe(document.querySelector('h1')),
        firstParagraph: probe(document.querySelector('p')),
        shadowChart: probe(card.querySelector('svg')),
        lightChart: probe(document.querySelector('svg[aria-label="Light chart"]')),
        frame: probe(frame),
        framedChart: probe(frame.contentDocument.querySelector('svg')),
        meta: probe(document.querySelector('meta')),
        hiddenFrameChart: probe(
            document.querySelector('iframe[title="Hidden frame"]').contentDocument.querySelector('svg')),
    };
}""".replace("__HEADING__", HEADING_JS)


@pytest.mark.browser
def test_the_heading_is_the_last_one_a_reader_passed() -> None:
    """It was the last in document order, which put the light chart under the
    page's <h1> and saw no heading in a shadow root or a frame."""
    found = _in_browser(
        FIXTURE.as_uri(), lambda page: page.evaluate(_HEADING_PROBES_JS)
    )
    title = {"text": "Visuals in shadow roots and frames", "level": 1}
    shadow = {"text": "Shadow section", "level": 2}
    framed = {"text": "Frame section", "level": 2}
    nothing = {"text": "", "level": 0}
    assert found == {
        # A heading is not the heading it sits under.
        "h1": nothing,
        "firstParagraph": title,
        "shadowChart": shadow,
        "lightChart": shadow,
        "frame": framed,
        "framedChart": framed,
        # Outside the reading order: not on the page, so under no heading.
        "meta": nothing,
        "hiddenFrameChart": nothing,
    }


@pytest.mark.browser
def test_the_heading_is_the_same_evaluated_inside_a_frame() -> None:
    """A locator inside a frame evaluates there, where `document` is the
    frame's. The walk started from that body and saw no heading before the
    frame (P16-16)."""

    async def body(page: Any) -> Any:
        framed = page.frame_locator('iframe[title="Figure frame"]').locator("svg")
        return await framed.evaluate(HEADING_JS)

    assert _in_browser(FIXTURE.as_uri(), body) == {"text": "Frame section", "level": 2}


#: Every harvested element, wherever it is, by asset id, with `HEADING_JS`'s
#: heading for it.
_ASSET_HEADINGS_JS = """() => {
    const heading = __HEADING__;
    const flat = __FLAT_TREE__;
    const found = {};
    for (const tree of flat.trees(document)) {
        for (const element of tree.querySelectorAll('[data-webshot-asset-id]')) {
            found[element.getAttribute('data-webshot-asset-id')] = heading(element).text;
        }
    }
    return found;
}""".replace("__HEADING__", HEADING_JS).replace("__FLAT_TREE__", FLAT_TREE_JS)


@pytest.mark.browser
def test_the_harvest_and_heading_js_agree_on_every_visual(tmp_path: Path) -> None:
    """Two walks would be two chances to disagree; there is one."""

    async def body(page: Any) -> tuple[dict[str, str], dict[str, str]]:
        assets = await _harvest(page, tmp_path)
        by_js: dict[str, str] = await page.evaluate(_ASSET_HEADINGS_JS)
        return {a.id: a.nearby_heading for a in assets}, by_js

    harvested, by_js = _in_browser(FIXTURE.as_uri(), body)
    assert len(harvested) == len(SHOWN)
    assert harvested == by_js


# --------------------------------------------------------------------------- #
# Scroll positions: the page prints as it stood
# --------------------------------------------------------------------------- #

_SCROLL_JS = """() => {
    const boxes = [...document.querySelectorAll('.box'),
                   ...document.getElementById('host').shadowRoot.querySelectorAll('.box')];
    const frame = document.querySelector('iframe[title="Capped frame"]').contentWindow;
    return {window: window.scrollY, boxes: boxes.map(box => box.scrollTop), frame: frame.scrollY};
}"""


@pytest.mark.browser
def test_the_harvest_puts_back_every_scroll_position_it_moved(tmp_path: Path) -> None:
    async def body(page: Any) -> tuple[Any, Any, int]:
        before = await page.evaluate(_SCROLL_JS)
        assets = await _harvest(page, tmp_path)
        return before, await page.evaluate(_SCROLL_JS), len(assets)

    before, after, harvested = _in_browser(SCROLLED.as_uri(), body)
    # Four charts, each out of view where it sits, so every one was scrolled
    # to, and the capped frame itself.
    assert harvested == 5
    assert before == {"window": 0, "boxes": [0, 0], "frame": 0}
    assert after == before


@pytest.mark.browser
def test_a_scrolling_box_prints_from_where_the_page_had_it(tmp_path: Path) -> None:
    """Chromium prints a scrolling box, and a frame, as it stands. Left
    scrolled to the chart below its fold, each box printed neither of its
    sentences. The direct test above covers every box the harvest moves;
    this one covers those the capture's release leaves scrolling."""
    output = tmp_path / "capture.pdf"
    report = tmp_path / "report.json"
    assert (
        main(
            [
                str(SCROLLED),
                "--output",
                str(output),
                "--no-ocr",
                "--report",
                str(report),
            ]
        )
        == 0
    )
    printed = _flat("\n".join(p.extract_text() or "" for p in PdfReader(output).pages))
    # The light box is released for print before the harvest (P16-10), so it
    # prints its top either way and its bottom as well. The shadow box is out
    # of the release's reach, so the restore is all that keeps its top.
    assert "Top of the light box: printed where it starts." in printed, printed
    assert "Top of the shadow box: printed where it starts." in printed, printed
    assert "Bottom of the shadow box" not in printed
    # A frame that could not be grown is scrolled back too.
    assert "Top of the capped frame: printed where it starts." in printed, printed
    assert "Bottom of the capped frame" not in printed
    warnings = json.loads(report.read_text("utf-8"))["warnings"]
    assert not [w for w in warnings if "Scroll positions" in w], warnings
