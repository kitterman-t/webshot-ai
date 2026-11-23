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
controls restyle the layer — with its old geometry, and at its old 0.1px size
without the top margin that kept it off a page's top edge — and require each
fixture to fail under the one it is about, so none of them passes by missing
the defect. At the layer's 0.5px size (docs/09 P135-4) the margin is no longer
needed, and a test says so.
"""

from __future__ import annotations

import asyncio
import io
from dataclasses import dataclass
from pathlib import Path

import pytest
from playwright.async_api import async_playwright
from pypdf import PdfReader

from webshot.capture.prepare import BASE_PRINT_CSS, adopt_stylesheet
from webshot.capture.visuals import VisualAsset, capture_visual_assets
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

#: The size the layer had before P135-4, with no margin: the geometry under
#: which P16-20 measured the page-top loss the margin was for.
OLD_SIZE_NO_MARGIN = """[data-webshot-ocr-text='true'] {
    margin: 0 !important; font-size: 0.1px !important; line-height: 0.1px !important;
}"""


class _FixedText:
    """An engine that recognizes the same text in every asset."""

    name = "fixed"
    label = "fixed"

    def applied_settings(self, *, language: str, psm: int) -> dict[str, object]:
        return {}

    def unavailable_reason(self, *, language: str = "") -> str | None:
        return None

    async def recognize(self, image: Path, *, language: str, psm: int) -> OCRResult:
        return OCRResult(text=RECOGNIZED, confidence=99.0, engine=self.label)


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
    #: The same again, at the layer's size before P135-4.
    pages_old_size_without_margin: list[str]


def _page_texts(pdf: bytes) -> list[str]:
    return [page.extract_text() for page in PdfReader(io.BytesIO(pdf)).pages]


def _has_sentinel(text: str) -> bool:
    # Set as small as the layer is, its words can lose some of the spaces between them in
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
            await adopt_stylesheet(page, BASE_PRINT_CSS)
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
            await adopt_stylesheet(page, NO_MARGIN)
            without_margin = _page_texts(await page.pdf(**print_geometry(options)))
            await adopt_stylesheet(page, OLD_SIZE_NO_MARGIN)
            old_size = _page_texts(await page.pdf(**print_geometry(options)))
            await adopt_stylesheet(page, OLD_GEOMETRY)
            old_geometry = await printed_width(page, options)
            return Printed(
                kinds=[asset.kind for asset in assets],
                layers=layers,
                bare=bare,
                layered=layered,
                old_geometry=old_geometry,
                pages=pages,
                pages_without_margin=without_margin,
                pages_old_size_without_margin=old_size,
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
def test_at_the_old_size_only_the_page_top_needs_the_margin(
    printed: tuple[Printed, Case],
) -> None:
    """The control for the margin, at the size P16-20 measured it: 0.1px
    text pushed to a page's top edge lost every glyph that does not dip below
    the baseline. Only the page-top fixture loses its text there, so the
    fixture still pushes its chart to the top. If it ever keeps its text,
    Chromium has stopped dropping those glyphs, or the fixture has stopped
    pushing the chart there, and the test should be looked at.
    """
    result, case = printed
    kept = any(_has_sentinel(text) for text in result.pages_old_size_without_margin)
    assert kept is not case.at_page_top


@pytest.mark.browser
def test_at_the_layer_s_size_no_page_needs_the_margin(
    printed: tuple[Printed, Case],
) -> None:
    """At 0.5px (P135-4) the layer keeps its text at a page's top edge with or
    without the margin; the margin stays as P16-20's guard."""
    result, _ = printed
    assert any(_has_sentinel(text) for text in result.pages)
    assert any(_has_sentinel(text) for text in result.pages_without_margin)


#: Three lines, as an engine reads a logo set over three.
LINES = "Harbor desk\nNorth quay watch\nTide tables"


class _Lines(_FixedText):
    async def recognize(self, image: Path, *, language: str, psm: int) -> OCRResult:
        return OCRResult(text=LINES, confidence=99.0, engine=self.label)


async def _print_spaced(fixture: Path, directory: Path) -> list[str]:
    options = CaptureOptions(source=str(fixture), output=directory / "unused.pdf")
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        try:
            page = await browser.new_page(viewport={"width": 1440, "height": 1000})
            await page.goto(fixture.as_uri())
            await adopt_stylesheet(page, BASE_PRINT_CSS)
            await page.emulate_media(media="print")
            assets, warnings, _ = await capture_visual_assets(
                page,
                directory / "assets",
                max_assets=10,
                ocr=True,
                ocr_language="eng",
                ocr_psm=3,
                engine=_Lines(),
            )
            assert not warnings, warnings
            assert [asset.kind for asset in assets] == ["img"]
            return _page_texts(await page.pdf(**print_geometry(options)))
        finally:
            await browser.close()


@pytest.mark.browser
def test_the_layer_reads_as_one_line_of_words_whatever_the_page_spaces(
    tmp_path: Path,
) -> None:
    """A logo's recognized lines, laid 0.1px apart, were read as one line with
    the words of each in turn; set inside a link the page spaced wide, each
    letter was read as a word (docs/09 P135-4). The layer is one line now,
    spaced as its font is, and its words read in order, whole."""
    [text] = asyncio.run(
        _print_spaced(FIXTURES / "ocr_layer_spaced_lines.html", tmp_path)
    )
    words = text.split()
    for word in ("recognized", "within", "visual", "asset", "watch", "tables"):
        assert word in words, text
    assert (
        "".join(words).find(
            "Textrecognizedwithinvisualassetasset-001:HarbordeskNorthquaywatchTidetables"
        )
        >= 0
    ), text


@pytest.mark.browser
def test_the_layer_is_text_but_not_legible_text(tmp_path: Path) -> None:
    """The clipping check asks whether the PDF prints a cut element's text,
    and a frame's layer, recognized at the window's width, answered for the
    print (docs/09 P135-4). The layer is in `page_texts` and not in
    `legible_page_texts`; the page's own text is in both, a caption set at 45%
    included, which a bound at half opaque left out (review of P135-4)."""
    from webshot.render.pdfium_bridge import legible_page_texts, page_texts

    fixture = FIXTURES / "ocr_layer_spaced_lines.html"
    options = CaptureOptions(source=str(fixture), output=tmp_path / "unused.pdf")

    async def run() -> bytes:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            try:
                page = await browser.new_page(viewport={"width": 1440, "height": 1000})
                await page.goto(fixture.as_uri())
                await adopt_stylesheet(page, BASE_PRINT_CSS)
                await page.emulate_media(media="print")
                await capture_visual_assets(
                    page,
                    tmp_path / "assets",
                    max_assets=10,
                    ocr=True,
                    ocr_language="eng",
                    ocr_psm=3,
                    engine=_Lines(),
                )
                return await page.pdf(**print_geometry(options))
            finally:
                await browser.close()

    pdf = asyncio.run(run())
    [every] = page_texts(pdf)
    [legible] = legible_page_texts(pdf)
    assert "Tide tables" in every
    assert "OCRLAYERBASELINE" in every and "OCRLAYERBASELINE" in legible
    # A muted caption is the page's text; the layer, at 1%, is not.
    assert "MUTEDCAPTION" in legible
    assert "Tide" not in legible
    assert "recognized" not in legible


class _Doubtful(_FixedText):
    """An engine that reads a headshot as an engine does: not words, and
    unsure of it."""

    async def recognize(self, image: Path, *, language: str, psm: int) -> OCRResult:
        return OCRResult(text="me)", confidence=30.01, engine=self.label)


@pytest.mark.browser
@pytest.mark.parametrize(
    ("engine", "layered"),
    [(_Doubtful(), False), (_FixedText(), True)],
    ids=["unsure", "sure"],
)
def test_a_reading_its_engine_doubts_stays_out_of_the_text_layer(
    tmp_path: Path, engine: _FixedText, layered: bool
) -> None:
    """A watch page's headshot was read as "me)" at 30% confidence, and a
    search of the PDF found it as the page's text (docs/09 P135-7). It stays
    in assets.json, a warning names it, and a reading the engine is sure of
    goes into the layer as before."""
    fixture = FIXTURES / "ocr_layer_right_edge.html"

    async def run() -> tuple[list[VisualAsset], list[str], int]:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            try:
                page = await browser.new_page(viewport={"width": 1440, "height": 1000})
                await page.goto(fixture.as_uri())
                await adopt_stylesheet(page, BASE_PRINT_CSS)
                await page.emulate_media(media="print")
                assets, warnings, _ = await capture_visual_assets(
                    page,
                    tmp_path / "assets",
                    max_assets=10,
                    ocr=True,
                    ocr_language="eng",
                    ocr_psm=3,
                    engine=engine,
                )
                layers = await page.locator("[data-webshot-ocr-text='true']").count()
                return assets, warnings, layers
            finally:
                await browser.close()

    assets, warnings, layers = asyncio.run(run())
    [asset] = assets
    assert asset.ocr.text in ("me)", RECOGNIZED)
    assert layers == (1 if layered else 0)
    if layered:
        assert warnings == []
    else:
        assert warnings == [
            "The text recognized in a visual asset is not in the PDF's text layer: "
            "asset-001 (30%). The OCR engine's mean confidence in it was under 70%, "
            "as it is in a reading of a photograph or an icon rather than of words, "
            "which a search of the PDF would find as the page's text. assets.json "
            "keeps it with its confidence."
        ]
