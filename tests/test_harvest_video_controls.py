"""A video's spinner that starts after the prepare stage (docs/09 P67).

Chromium's native controls show a spinner while a video loads, and when loading
stops they let it finish its turn, which ends only when the renderer dispatches
`animationend`. The prepare stage ends the turn every video starts with the
page (P66), but a load can end after it: a `preload="metadata"` request that
answers or fails later starts a last turn then, and the harvest photographed
it, in the player's asset and in that of a frame holding the player, which the
harvest photographs first.

The race is reproduced here rather than waited for, with the harvest alone, as
if after the stage. Re-setting a video's `src` re-runs the load it made with
the page, and the DevTools protocol slows every animation in the page, the
controls' own included, so the spinner's last turn takes about a minute
instead of a second. `fails_without_the_fix` is the precondition: a plain
screenshot taken then must show the spinner, or the test has reproduced
nothing and says so.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from playwright.async_api import Locator, Page, Route, async_playwright

from webshot.capture.visuals import capture_visual_assets
from webshot.ocr.tesseract import TesseractEngine

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "video_controls_harvest.html"

#: What the harvest passes, so a reference is the image it would take.
SCREENSHOT = {"animations": "disabled", "caret": "hide", "scale": "device"}

#: A fraction of the spinner's turn, so a spinner still turning moves between
#: any two shots this far apart and cannot pass for settled.
SETTLE_INTERVAL_S = 0.25
SETTLE_SHOTS = 4
SETTLE_LIMIT_S = 30.0

#: The page's animation clock during the race: 1/50 of real time.
SLOW = 0.02

#: The fetched video's request has failed: the browser has given up on it.
NO_SOURCE = "video => video.networkState === HTMLMediaElement.NETWORK_NO_SOURCE"

#: The fetched video's request is out and has not been answered.
LOADING = "video => video.networkState === HTMLMediaElement.NETWORK_LOADING"

FRAME = (
    "is a video. Its current visual frame was preserved; temporal and audio "
    "content require a source transcript or separate media processing."
)
STILL_LOADING = (
    "is a video that was still loading when it was captured, so its image shows "
    "the browser's loading indicator rather than a frame of the video, and can "
    "differ between captures; temporal and audio content require a source "
    "transcript or separate media processing."
)


async def _settled(video: Locator) -> bytes:
    """The video's image once its controls stop changing.

    Read by comparison, not by waiting a fixed time, so a slow machine waits
    longer instead of recording a spinner as the reference.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + SETTLE_LIMIT_S
    shots = [await video.screenshot(**SCREENSHOT)]
    while len(shots) < SETTLE_SHOTS or len(set(shots[-SETTLE_SHOTS:])) > 1:
        if loop.time() > deadline:
            raise AssertionError(
                f"the video's image kept changing for {SETTLE_LIMIT_S:.0f} s: "
                f"{len(set(shots))} distinct images in {len(shots)} shots"
            )
        await asyncio.sleep(SETTLE_INTERVAL_S)
        shots.append(await video.screenshot(**SCREENSHOT))
    return shots[-1]


async def _harvest(page: Page, directory: Path) -> tuple[list[Any], list[str]]:
    assets, warnings, unsaved = await capture_visual_assets(
        page,
        directory,
        max_assets=50,
        ocr=False,
        ocr_language="eng",
        ocr_psm=3,
        engine=TesseractEngine(),
    )
    assert unsaved == 0
    return assets, warnings


#: Re-run the load the video made with the page.
RELOAD = "video => video.setAttribute('src', video.getAttribute('src'))"


def _race(directory: Path) -> dict[str, Any]:
    async def run() -> dict[str, Any]:
        requested: list[str] = []

        async def fail(route: Route) -> None:
            requested.append(route.request.url)
            await route.abort()

        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            try:
                page = await browser.new_page()
                await page.route("https://media.example/**", fail)
                await page.goto(FIXTURE.as_uri())
                videos = page.locator("video")
                deferred, fetched = videos.nth(0), videos.nth(1)
                frame = page.locator("iframe")
                framed = page.frame_locator("iframe").locator("video")
                # The fetched video's request fails, which ends its loading
                # too: the same last turn, started by the error.
                await page.wait_for_function(
                    NO_SOURCE, arg=await fetched.element_handle()
                )
                shown = (deferred, fetched, frame, framed)
                references = [await _settled(visual) for visual in shown]

                protocol = await page.context.new_cdp_session(page)
                await protocol.send("Animation.enable")
                await protocol.send("Animation.setPlaybackRate", {"playbackRate": SLOW})
                for video in (deferred, fetched, framed):
                    await video.evaluate(RELOAD)
                await page.wait_for_function(
                    NO_SOURCE, arg=await fetched.element_handle()
                )
                fails_without_the_fix = [
                    await visual.screenshot(**SCREENSHOT) != reference
                    for visual, reference in zip(shown, references, strict=True)
                ]
                states = [
                    await video.evaluate(
                        "video => [video.networkState, video.readyState]"
                    )
                    for video in (deferred, fetched, framed)
                ]
                assets, warnings = await _harvest(page, directory)
            finally:
                await browser.close()
        return {
            "requested": requested,
            "fails_without_the_fix": fails_without_the_fix,
            "states": states,
            "assets": [(asset.id, asset.kind, asset.title) for asset in assets],
            "matches": [
                (directory / f"{asset.id}.png").read_bytes() == reference
                for asset, reference in zip(assets, references, strict=True)
            ],
            "warnings": warnings,
        }

    return asyncio.run(run())


@pytest.mark.browser
def test_a_finished_load_leaves_no_spinner_in_the_asset(tmp_path: Path) -> None:
    raced = _race(tmp_path / "assets")
    assert raced["fails_without_the_fix"] == [True] * 4, (
        "the spinner was not on screen in every visual when the harvest "
        f"began ({raced['fails_without_the_fix']}), so this run reproduced "
        "nothing there; was the animation clock slowed?"
    )
    # None is loading: two deferred their load, the other's request failed.
    assert raced["states"] == [[1, 0], [3, 0], [1, 0]]
    assert raced["requested"] == ["https://media.example/fetched.mp4"] * 2, (
        "a deferred video is never requested; the fetched one, once per load"
    )
    assert raced["assets"] == [
        ("asset-001", "video", "Deferred recording"),
        ("asset-002", "video", "Fetched recording"),
        ("asset-003", "iframe", "Framed recording"),
        ("asset-004", "video", "Recording in a frame"),
    ]
    assert raced["matches"] == [True] * 4, (
        "each asset must be its settled image, not a turn of a spinner: "
        f"{raced['matches']}"
    )
    assert raced["warnings"] == [
        f"asset-001 {FRAME}",
        f"asset-002 {FRAME}",
        f"asset-004 {FRAME}",
    ]


def _held(directory: Path) -> dict[str, Any]:
    async def run() -> dict[str, Any]:
        held: list[Route] = []

        async def hold(route: Route) -> None:
            held.append(route)

        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            try:
                page = await browser.new_page()
                await page.route("https://media.example/**", hold)
                await page.goto(FIXTURE.as_uri())
                fetched = page.locator("video").nth(1)
                await page.wait_for_function(
                    LOADING, arg=await fetched.element_handle()
                )
                states = await page.locator("video").evaluate_all(
                    "videos => videos.map(video => [video.networkState, video.readyState])"
                )
                assets, warnings = await _harvest(page, directory)
                for route in held:
                    await route.abort()
            finally:
                await browser.close()
        return {
            "requested": [route.request.url for route in held],
            "states": states,
            "assets": len(assets),
            "warnings": warnings,
        }

    return asyncio.run(run())


@pytest.mark.browser
def test_a_video_still_loading_is_reported_not_called_a_frame(tmp_path: Path) -> None:
    held = _held(tmp_path / "assets")
    # The fetched video's request never answers, so it is loading throughout.
    assert held["requested"] == ["https://media.example/fetched.mp4"]
    assert held["states"] == [[1, 0], [2, 0]]
    assert held["assets"] == 4
    assert held["warnings"] == [
        f"asset-001 {FRAME}",
        f"asset-002 {STILL_LOADING}",
        f"asset-004 {FRAME}",
    ]
