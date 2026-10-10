"""A frame the page removes during the capture does not end it (docs/09 P23-2).

A news page's ad slots come and go while the capture runs. The release of
clipped text listed the page's frames and then scripted each one, and a frame
removed in between raised `Frame.evaluate: Frame was detached`, which ended
the capture with exit 5. The race is made certain here: the frames are listed,
then the page removes one, then the release runs over the stale list.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Frame, Page, async_playwright

import webshot.capture.prepare as prepare

ROWS = "".join(f"<p>Row {n} of the station log.</p>" for n in range(30))
FRAME = f"<div style='max-height:6em;overflow-y:auto'>{ROWS}</div>"
PAGE = (
    "<!doctype html><title>Station</title><p>Page text.</p>"
    f'<iframe id="kept" srcdoc="{FRAME}" style="height:200px"></iframe>'
    f'<iframe id="ad" srcdoc="{FRAME}" style="height:200px"></iframe>'
)


async def _release_after_removal(monkeypatch: pytest.MonkeyPatch) -> tuple[int, bool]:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        try:
            page = await browser.new_page()
            await page.set_content(PAGE)
            listed = await prepare._scriptable_frames(page)
            assert len(listed) == 2

            async def stale(_: Page) -> list[Frame]:
                return listed

            monkeypatch.setattr(prepare, "_scriptable_frames", stale)
            await page.evaluate("() => document.getElementById('ad').remove()")
            await page.wait_for_function("() => !document.getElementById('ad')")
            released = await prepare.release_clamped_text(page)
            return released, listed[1].is_detached()
        finally:
            await browser.close()


@pytest.mark.browser
def test_a_frame_removed_before_its_release_is_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    released, detached = asyncio.run(_release_after_removal(monkeypatch))
    assert detached, "the race did not happen; the test proves nothing"
    assert released == 1, "the frame that stayed is still released"


@pytest.mark.browser
def test_any_other_failure_in_a_frame_still_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only a frame that is gone is skipped: a broken script in a live one is a bug."""

    async def broken(target: Any) -> tuple[int, int]:
        if isinstance(target, Frame):
            raise PlaywrightError("Evaluation failed: ReferenceError")
        return 0, 0

    async def scenario() -> None:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            try:
                page = await browser.new_page()
                await page.set_content(PAGE)
                monkeypatch.setattr(prepare, "_release_in", broken)
                with pytest.raises(PlaywrightError, match="ReferenceError"):
                    await prepare.release_clamped_text(page)
            finally:
                await browser.close()

    asyncio.run(scenario())
