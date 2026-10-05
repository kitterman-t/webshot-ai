"""The OCR text layer must not move the layout it describes (docs/09 P16-20).

The visual-asset stage puts an invisible span holding each asset's recognized
text after the asset, so a search of the PDF finds text that exists only as
pixels. The span was 600px wide from where it sat, right after the asset. After
anything near the right edge — an image, an embedded frame — that ran past the
page, and Chromium's shrink-to-fit printed the whole page wider and smaller to
take it in. After an image at the edge the layer was cut off anyway, so its
text never reached the PDF it had just shrunk. And where the asset had been
pushed to the top of a page, the layer's 0.1px text printed only the glyphs
that dip below the baseline.

These tests run the real stage over three fixtures with an engine that returns
fixed text, and hold both halves: the width Chromium prints at is the same with
the layer as without it, and the layer's text is on the asset's page. Two
controls restyle the layer — with its old geometry, and without the top margin
that keeps it off a page's top edge — and require each fixture to fail under
the one it is about, so none of them passes by missing the defect.
"""

from __future__ import annotations

import asyncio
import io
from dataclasses import dataclass
from pathlib import Path

import pytest
from playwright.async_api import async_playwright
from pypdf import PdfReader

from webshot.capture.prepare import BASE_PRINT_CSS
from webshot.capture.visuals import capture_visual_assets
from webshot.config import CaptureOptions
from webshot.ocr.engine import OCRResult
from webshot.render.clipping import printed_width
from webshot.render.pdf import print_geometry

FIXTURES = Path(__file__).parent / "fixtures"

#: Letter at 0.6in margins and scale 0.9: 526pt of content is 779 CSS px.
PAPER_WIDTH = 779

RECOGNIZED = "OCRLAYERSENTINEL visits by month"

#: The geometry the layer had before P16-20, laid over the current rule.
OLD_GEOMETRY = """[data-webshot-ocr-text='true'] {
    left: auto !important; right: auto !important; width: 600px !important;
}"""

#: The current rule less the top margin that keeps the text off a page's edge.
NO_MARGIN = "[data-webshot-ocr-text='true'] { margin: 0 !important; }"


class _FixedText:
    """An engine that recognizes the same text in every asset."""

    name = "fixed"
    label = "fixed"

    def applied_settings(self, *, language: str, psm: int) -> dict[str, object]:
        return {}

    def unavailable_reason(self, *, language: str = "") -> str | None:
        return None

    async def recognize(self, image: Path, *, language: str, psm: int) -> OCRResult:
        return OCRResult(text=RECOGNIZED, confidence=0.99, engine=self.label)


@dataclass(frozen=True, slots=True)
class Case:
    fixture: str
    kind: str
    #: Narrower than the paper, so printed bare at the paper's width.
    fits_paper: bool
    #: The printed page the asset, and so its recognized text, is on.
    page: int
    #: Pushed to the top of that page, where the layer needs its margin.
    at_page_top: bool


CASES = [
    Case("ocr_layer_right_edge.html", "img", True, 1, False),
    # P16-1's column: wider than the paper, so shrunk to fit before the layer.
    Case("ocr_layer_framed_column.html", "iframe", False, 1, False),
    Case("ocr_layer_page_top.html", "img", True, 2, True),
]


@dataclass(frozen=True, slots=True)
class Printed:
    kinds: list[str]
    layers: int
    bare: int | None
    layered: int | None
    old_geometry: int | None
    #: The text of each printed page, with the layer as it ships.
    pages: list[str]
    #: The same, with the layer's top margin taken away.
    pages_without_margin: list[str]


def _page_texts(pdf: bytes) -> list[str]:
    return [page.extract_text() for page in PdfReader(io.BytesIO(pdf)).pages]


def _has_sentinel(text: str) -> bool:
    # Set at 0.1px, the layer's words lose some of the spaces between them in
    # extraction, so the sentinel is looked for without them.
    return "OCRLAYERSENTINEL" in text.replace(" ", "")


async def _print(fixture: Path, directory: Path) -> Printed:
    options = CaptureOptions(source=str(fixture), output=directory / "unused.pdf")
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        try:
            page = await browser.new_page(viewport={"width": 1440, "height": 1000})
            await page.goto(fixture.as_uri())
            # As the pipeline leaves the page for the visual-asset stage.
            await page.add_style_tag(content=BASE_PRINT_CSS)
            await page.emulate_media(media="print")
            bare = await printed_width(page, options)
            assets, warnings, _ = await capture_visual_assets(
                page,
                directory / "assets",
                max_assets=10,
                ocr=True,
                ocr_language="eng",
                ocr_psm=3,
                engine=_FixedText(),
            )
            assert not warnings, warnings
            layers = await page.locator("[data-webshot-ocr-text='true']").count()
            layered = await printed_width(page, options)
            pages = _page_texts(await page.pdf(**print_geometry(options)))
            await page.add_style_tag(content=NO_MARGIN)
            without_margin = _page_texts(await page.pdf(**print_geometry(options)))
            await page.add_style_tag(content=OLD_GEOMETRY)
            old_geometry = await printed_width(page, options)
            return Printed(
                kinds=[asset.kind for asset in assets],
                layers=layers,
                bare=bare,
                layered=layered,
                old_geometry=old_geometry,
                pages=pages,
                pages_without_margin=without_margin,
            )
        finally:
            await browser.close()


@pytest.fixture(
    scope="module",
    params=CASES,
    ids=["image-at-right-edge", "frame-in-pushed-column", "image-at-page-top"],
)
def printed(
    request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory
) -> tuple[Printed, Case]:
    """One run per fixture, reused: each drives a real browser."""
    case: Case = request.param
    result = asyncio.run(
        _print(FIXTURES / case.fixture, tmp_path_factory.mktemp("ocr-layer"))
    )
    # The layer exists, once, for the asset the fixture is about; without it
    # every test below would pass by measuring a page with nothing added.
    assert result.kinds == [case.kind]
    assert result.layers == 1
    return result, case


@pytest.mark.browser
def test_the_layer_does_not_change_the_printed_width(
    printed: tuple[Printed, Case],
) -> None:
    result, _ = printed
    assert result.bare is not None, "the print-width probe did not answer"
    assert result.layered == result.bare


@pytest.mark.browser
def test_the_fixture_prints_bare_as_described(printed: tuple[Printed, Case]) -> None:
    """Each fixture's comment says how wide it prints without the layer."""
    result, case = printed
    assert result.bare is not None
    if case.fits_paper:
        assert result.bare == PAPER_WIDTH
    else:
        assert result.bare > PAPER_WIDTH


@pytest.mark.browser
def test_the_old_geometry_moves_the_width(printed: tuple[Printed, Case]) -> None:
    """The control: each fixture fails the width test under the old rule."""
    result, _ = printed
    assert result.bare is not None and result.old_geometry is not None
    assert result.old_geometry > result.bare


@pytest.mark.browser
def test_the_recognized_text_is_on_the_asset_s_page(
    printed: tuple[Printed, Case],
) -> None:
    """Its purpose is a search over recognized text, so it must be printed.

    On the asset's own page, so a search lands where the picture is. The
    baseline paragraph, on the first page, shows the text layer is readable.
    """
    result, case = printed
    assert "OCRLAYERBASELINE" in result.pages[0]
    found = [n for n, text in enumerate(result.pages, 1) if _has_sentinel(text)]
    assert found == [case.page]


@pytest.mark.browser
def test_only_the_page_top_needs_the_margin(printed: tuple[Printed, Case]) -> None:
    """The control for the margin: without it the pushed chart's text is lost.

    And only that one. If the page-top fixture ever keeps its text without the
    margin, Chromium has stopped dropping glyphs at a page's top edge, or the
    fixture has stopped pushing the chart there; either way this test no
    longer shows the margin doing anything, and it should be looked at.
    """
    result, case = printed
    kept = any(_has_sentinel(text) for text in result.pages_without_margin)
    assert kept is not case.at_page_top
