"""`--max-assets` counts the visuals a page shows (docs/09 P20-3).

The cap was applied to the harvest's candidate list, before each candidate was
checked: hidden elements and icons smaller than 32 by 24 pixels took places,
were skipped, and the visuals after them were dropped. A mirrored Wikipedia
page warned that it "contains 65 visual assets; only the first 50 were
captured" with 8 assets in the bundle.

`hidden_and_tiny_visuals.html` puts 40 hidden pictures and 25 icons before
three charts a reader sees: 68 candidates, three of them visual assets.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Locator, async_playwright

from webshot.capture.visuals import capture_visual_assets
from webshot.ocr.tesseract import TesseractEngine

FIXTURES = Path(__file__).resolve().parent / "fixtures"
FIXTURE = FIXTURES / "hidden_and_tiny_visuals.html"
IN_FRAMES = FIXTURES / "visuals_past_cap_in_frames.html"


def _harvest(
    directory: Path, max_assets: int, fixture: Path = FIXTURE
) -> dict[str, Any]:
    async def run() -> dict[str, Any]:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            try:
                page = await browser.new_page()
                await page.goto(fixture.as_uri())
                assets, warnings, unsaved = await capture_visual_assets(
                    page,
                    directory / "assets",
                    max_assets=max_assets,
                    ocr=False,
                    ocr_language="eng",
                    ocr_psm=3,
                    engine=TesseractEngine(),
                )
            finally:
                await browser.close()
        return {
            "labels": [a.aria_label for a in assets],
            "warnings": warnings,
            "unsaved": unsaved,
        }

    return asyncio.run(run())


@pytest.mark.browser
def test_hidden_and_tiny_candidates_take_no_place(tmp_path: Path) -> None:
    harvested = _harvest(tmp_path, max_assets=50)
    assert harvested["labels"] == ["First chart", "Second chart", "Third chart"]
    assert harvested["warnings"] == []
    assert harvested["unsaved"] == 0


@pytest.mark.browser
def test_the_warning_counts_the_visuals_the_cap_left_out(tmp_path: Path) -> None:
    harvested = _harvest(tmp_path, max_assets=2)
    assert harvested["labels"] == ["First chart", "Second chart"]
    assert harvested["warnings"] == [
        "The page shows 1 more visual asset than the limit of 2 (--max-assets); "
        "it was not captured."
    ]
    # Returned as well as written, for the empty-capture check (docs/09 P22-2).
    assert harvested["unsaved"] == 1


@pytest.mark.browser
def test_a_cap_of_zero_counts_every_visual(tmp_path: Path) -> None:
    harvested = _harvest(tmp_path, max_assets=0)
    assert harvested["labels"] == []
    assert harvested["warnings"] == [
        "The page shows 3 more visual assets than the limit of 0 (--max-assets); "
        "they were not captured."
    ]
    assert harvested["unsaved"] == 3


@pytest.mark.browser
def test_a_visual_whose_capture_failed_is_counted_as_unsaved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed capture is a picture the bundle lacks, not a page without one.

    Counted for the empty-capture check, which must not say "no image" of a
    page whose images could not be saved (docs/09 P22-2).
    """

    async def refuse(self: Locator, **_: Any) -> bytes:
        raise PlaywrightError("element is not attached to the DOM")

    monkeypatch.setattr(Locator, "screenshot", refuse)
    harvested = _harvest(tmp_path, max_assets=50)
    assert harvested["labels"] == []
    assert len(harvested["warnings"]) == 3, harvested["warnings"]
    assert all(
        warning.startswith("Could not capture visual element")
        for warning in harvested["warnings"]
    )
    assert harvested["unsaved"] == 3


@pytest.mark.browser
@pytest.mark.parametrize(
    ("max_assets", "kept", "left_out"),
    [(0, 0, 5), (1, 1, 4), (3, 3, 2)],
)
def test_visuals_past_the_cap_are_counted_in_frames_too(
    tmp_path: Path, max_assets: int, kept: int, left_out: int
) -> None:
    """The candidates past the cap are counted in one call per frame, by the
    same test the harvest applies one at a time; the frame's hidden chart and
    its icon are not visual assets, and the frame itself is."""
    harvested = _harvest(tmp_path, max_assets=max_assets, fixture=IN_FRAMES)
    assert len(harvested["labels"]) == kept
    assert harvested["warnings"] == [
        f"The page shows {left_out} more visual assets than the limit of "
        f"{max_assets} (--max-assets); they were not captured."
    ]
