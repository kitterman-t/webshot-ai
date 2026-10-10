"""A visual the page removes while it is being captured (docs/09 P23-2).

A news page's ad slots are refilled while the capture runs. The harvest finds
each visual, screenshots it, then writes its asset id into the page through a
Playwright locator, and every locator call waits for its element, by default
for the whole operation timeout (300 s in a capture). A visual removed after
its screenshot made that last call wait the full timeout, so the bundle stage
sat for more than seven minutes, and the asset was then listed as saved while
also counted as a failure. Here the page removes the second chart the moment
its screenshot is taken, and the context waits as long as a capture's does.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

import pytest
from playwright.async_api import Locator, async_playwright

from webshot.capture.visuals import FOUND_ELEMENT_TIMEOUT_MS, capture_visual_assets
from webshot.config import DEFAULT_OPERATION_TIMEOUT_MS
from webshot.ocr.tesseract import TesseractEngine

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "hidden_and_tiny_visuals.html"


def _harvest_while_one_vanishes(
    directory: Path, monkeypatch: pytest.MonkeyPatch
) -> dict[str, Any]:
    screenshot = Locator.screenshot

    async def then_removed(self: Locator, **kwargs: Any) -> bytes:
        taken = await screenshot(self, **kwargs)
        if await self.get_attribute("aria-label") == "Second chart":
            await self.evaluate("element => element.remove()")
        return taken

    monkeypatch.setattr(Locator, "screenshot", then_removed)

    async def run() -> dict[str, Any]:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            try:
                context = await browser.new_context()
                context.set_default_timeout(DEFAULT_OPERATION_TIMEOUT_MS)
                page = await context.new_page()
                await page.goto(FIXTURE.as_uri())
                started = time.monotonic()
                assets, warnings, unsaved = await capture_visual_assets(
                    page,
                    directory,
                    max_assets=50,
                    ocr=False,
                    ocr_language="eng",
                    ocr_psm=3,
                    engine=TesseractEngine(),
                )
                elapsed = time.monotonic() - started
            finally:
                await browser.close()
        return {
            "assets": [(a.id, a.aria_label) for a in assets],
            "files": sorted(p.name for p in directory.iterdir()),
            "warnings": warnings,
            "unsaved": unsaved,
            "elapsed": elapsed,
        }

    return asyncio.run(run())


@pytest.mark.browser
def test_a_visual_removed_mid_capture_costs_seconds_and_is_not_listed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harvested = _harvest_while_one_vanishes(tmp_path / "assets", monkeypatch)
    # One short wait for the vanished chart, not the operation timeout.
    assert harvested["elapsed"] < FOUND_ELEMENT_TIMEOUT_MS / 1000 + 15
    assert harvested["assets"] == [
        ("asset-001", "First chart"),
        ("asset-002", "Third chart"),
    ]
    assert harvested["files"] == ["asset-001.png", "asset-002.png"], (
        "no file left behind for an asset the bundle does not list"
    )
    assert harvested["warnings"] == [
        "Visual element 67 was removed by the page while it was being captured, "
        "so it is in neither the PDF nor the bundle."
    ]
    assert harvested["unsaved"] == 1
