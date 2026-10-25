"""A gradient layer a repeat adds nothing to prints as one blob, not a page of them.

Skia's PDF backend rasterizes a translucent or tiled gradient over the whole
printable page, once per layer per box, and a tiled one fills that raster
with tiles of which only the box is ever shown (docs/09 P103). The buttons in
`layered_gradient_buttons.html` printed three such page-filling rasters;
`render/gradients.py` untiles them before the render. The fixture's text and
colours are made up.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from playwright.async_api import async_playwright
from pypdf import PdfReader

from webshot.render.gradients import untile_gradients

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = REPO_ROOT / "tests" / "fixtures"
BUTTONS = FIXTURES / "layered_gradient_buttons.html"
KEPT = FIXTURES / "layered_gradient_kept.html"
CANVAS = FIXTURES / "layered_gradient_canvas.html"

#: A raster whose visible pixels cover more of it than this is painted outside
#: its box: each button is about 3% of the printable page, and a page of its
#: tiles covered 43%.
MOST_COVERED = 0.10


def _resolved(value: Any) -> Any:
    """A PDF reference followed, or an empty dictionary for one that is not there."""
    return value.get_object() if value is not None else {}


def _raster_coverages(pdf: Path) -> list[float]:
    """For each soft-masked raster a pattern paints, the share of its pixels
    that are not fully transparent."""
    shares: list[float] = []
    for page in PdfReader(str(pdf)).pages:
        resources: Any = page["/Resources"].get_object()
        for pattern in _resolved(resources.get("/Pattern")).values():
            xobjects = _resolved(_resolved(pattern).get("/Resources")).get("/XObject")
            for image in _resolved(xobjects).values():
                image = image.get_object()
                if image.get("/Subtype") != "/Image" or "/SMask" not in image:
                    continue
                width, height = int(image["/Width"]), int(image["/Height"])
                if width < 2 or height < 2:
                    continue
                alpha = image["/SMask"].get_object().get_data()
                shares.append(sum(1 for value in alpha if value) / (width * height))
    return shares


@pytest.fixture(scope="module")
def printed(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    folder = tmp_path_factory.mktemp("untiled-gradients")
    pdfs: dict[str, Path] = {}
    for name, page in (("buttons", BUTTONS), ("kept", KEPT)):
        pdf = folder / f"{name}.pdf"
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "webshot",
                str(page),
                "--output",
                str(pdf),
                "--no-ocr",
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr
        pdfs[name] = pdf
    return pdfs


@pytest.mark.browser
def test_no_button_prints_a_page_of_tiles(printed: dict[str, Path]) -> None:
    # The boxes the pass keeps still repeat, so Chromium still prints each of
    # their layers as a page of tiles. The walk finding those is what lets an
    # empty list below mean "no page of tiles" rather than "the walk saw
    # nothing": an untiled layer prints as a small raster on macOS and as a
    # vector shading, with no raster at all, on Linux (docs/09 P103-3).
    kept = _raster_coverages(printed["kept"])
    assert max(kept, default=0.0) > MOST_COVERED, sorted(kept, reverse=True)
    shares = _raster_coverages(printed["buttons"])
    assert max(shares, default=0.0) <= MOST_COVERED, sorted(shares, reverse=True)


async def _repeats_after_untiling() -> tuple[int, dict[str, str]]:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        try:
            page = await browser.new_page()
            await page.goto(KEPT.as_uri())
            await page.emulate_media(media="print")
            count = await untile_gradients(page)
            repeats = await page.evaluate(
                """() => Object.fromEntries([...document.querySelectorAll('a.swatch')].map(a => [
                    a.textContent,
                    a.classList.contains('untiled-before')
                        ? getComputedStyle(a, '::before').backgroundRepeat
                        : getComputedStyle(a).backgroundRepeat]))"""
            )
            return count, repeats
        finally:
            await browser.close()


@pytest.mark.browser
def test_only_a_repeat_nobody_sees_is_removed() -> None:
    count, repeats = asyncio.run(_repeats_after_untiling())
    assert count == 2, repeats
    assert repeats == {
        "Untiled element": "no-repeat, no-repeat",
        "Untiled pseudo-element": "no-repeat, no-repeat",
        "Dashed border": "repeat, repeat",
        "Translucent border": "repeat, repeat",
        "Patterned": "repeat, repeat",
        "Offset": "repeat, repeat",
        "Content origin": "repeat, repeat",
        "Already not repeating": "no-repeat, no-repeat",
    }


async def _canvas_repeats(root_color: str | None) -> tuple[int, str, str]:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        try:
            page = await browser.new_page()
            await page.goto(CANVAS.as_uri())
            if root_color is not None:
                await page.add_style_tag(
                    content=f"html {{ background-color: {root_color}; }}"
                )
            await page.emulate_media(media="print")
            count = await untile_gradients(page)
            html, body = await page.evaluate(
                "() => [document.documentElement, document.body].map("
                "e => getComputedStyle(e).backgroundRepeat)"
            )
            return count, html, body
        finally:
            await browser.close()


@pytest.mark.browser
def test_a_background_that_paints_the_canvas_keeps_its_repeat() -> None:
    """The root's background, or the body's when the root has none, paints the
    whole canvas from a tile the root's box sizes, so its repeat is what fills
    a short page below its content (CSS Backgrounds 3, 2.11.2)."""
    assert asyncio.run(_canvas_repeats(None)) == (0, "repeat", "repeat, repeat")
    # With a root background of its own, the body's paints only the body's
    # box, which its tile covers, and is untiled like any other box's.
    assert asyncio.run(_canvas_repeats("rgb(250, 250, 250)")) == (
        1,
        "repeat",
        "no-repeat, no-repeat",
    )
